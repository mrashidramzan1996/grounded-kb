"""Baseline eval: ask a coding agent the questions in questions.yaml, headless and read-only, and score it.

Run by hand, never in CI (it calls a real agent):

    uv run python eval/run.py --agent claude --with-kb  --repo /path/to/acme-billing
    uv run python eval/run.py --agent claude --without-kb --repo /path/to/acme-billing

--with-kb tells the agent to use `kb context` / `kb trace` and to cite record ids; --without-kb tells it to answer from
the documents under docs/, run in a temporary copy of the repository without the top-level kb/, .kb/, .claude/ and
.codex/ and with the kb blocks removed from AGENTS.md and CLAUDE.md, so nothing of the KB can reach the baseline.
The agent is sandboxed read-only: only Read, Grep and Glob (plus `kb context` / `kb trace` with --with-kb), no writes,
no MCP; claude loads only project settings (--setting-sources project).
The questions and the repository content the agent reads are sent to the agent's model provider, and the answers are
written to eval/results/*.json. The run refuses to start unless the repository has a kb.config.yaml and registered
sources and `kb check` reports no source.secret_in_original. Results are merged into
eval/results/phase1-<agent>.json, one entry per mode:
  wrong_fact_rate: answers missing an expected fact or holding a forbidden one / questions
  citation_rate:   answers naming a record id or source document from `cites` / questions
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from kb.check import run_check
from kb.records.repo import KbRepo
from kb.textblocks import remove_block
from kb.yamlio import load_yaml_strict

HERE = Path(__file__).parent
AGENTS = ("claude", "codex")
Runner = Callable[[list[str], Path], str]

PROMPT = {
    True: (
        "Answer from this repository's knowledge base. Run `kb context --id <ID>` / `kb context --module <ID>` and "
        "`kb trace <ID>` to read it, and cite the record IDs (REQ-, ADR-, INV-) your answer rests on. "
        "If the KB does not answer, say so.\n\nQuestion: {question}"
    ),
    False: (
        "Answer from the product documents under docs/. Do not read the kb/ directory. Name the document you used. "
        "If the documents do not answer, say so.\n\nQuestion: {question}"
    ),
}


KB_COMMANDS = ["Bash(kb context:*)", "Bash(kb trace:*)"]
# kept out of the without-kb copy (top level only: a nested docs/kb/ is product content)
KB_TRACES = {"kb", ".kb", ".claude", ".codex"}
KB_BLOCK_FILES = ("AGENTS.md", "CLAUDE.md")


def build_command(agent: str, question: str, with_kb: bool) -> list[str]:
    prompt = PROMPT[with_kb].format(question=question)
    if agent == "claude":
        # --tools limits what exists; dontAsk refuses everything not allowed (no prompts, no writes); no MCP servers.
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk", "--strict-mcp-config"]
        cmd += ["--setting-sources", "project"]  # no user settings, hooks or memory from this machine
        cmd += ["--tools", "Read,Grep,Glob" + (",Bash" if with_kb else "")]
        cmd += ["--allowedTools", *(["Read", "Grep", "Glob"] + (KB_COMMANDS if with_kb else []))]
        cmd += ["--disallowedTools", *(["Edit", "Write", "NotebookEdit"] + ([] if with_kb else ["Bash"]))]
        return cmd
    if agent == "codex":
        return ["codex", "exec", "--sandbox", "read-only", "--json", prompt]
    raise ValueError(f"unknown agent {agent!r}: use {', '.join(AGENTS)}")


def extract_answer(agent: str, stdout: str) -> str:
    """The agent's final answer text from its headless JSON output ('' when there is none)."""
    if agent == "claude":
        try:
            data = json.loads(stdout)
        except ValueError:
            return ""
        return str(data.get("result", "")) if isinstance(data, dict) else ""
    answer = ""  # codex: JSON lines; the last agent message wins
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        msg = event.get("msg") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "agent_message":
            answer = str(item.get("text", ""))
        elif isinstance(msg, dict) and msg.get("type") == "agent_message":
            answer = str(msg.get("message", ""))
    return answer


def result_error(agent: str, stdout: str) -> str | None:
    """Why a run that exited 0 still failed: claude reports API and limit errors as `is_error: true`."""
    if agent != "claude":
        return None
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("is_error") is True:
        return f"is_error: {' '.join(str(data.get('result', '')).split())[:300]}"
    return None


def _has(text: str, term: str) -> bool:
    """Whole-word / whole-phrase, case-insensitive match ('no' does not match 'know')."""
    return re.search(r"(?<!\w)" + re.escape(term.strip().lower()) + r"(?!\w)", text.lower()) is not None


def is_wrong(question: dict, answer: str) -> bool:
    missing = any(not any(_has(answer, alt) for alt in group) for group in question["expect"])
    return missing or any(_has(answer, f) for f in question.get("forbid", []))


def is_cited(question: dict, answer: str) -> bool:
    return any(c in answer for c in question["cites"])


class RunnerError(Exception):
    """The agent could not be run, timed out or exited non-zero."""


def subprocess_runner(cmd: list[str], cwd: Path) -> str:
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RunnerError(f"{type(e).__name__}: {e}") from e
    if done.returncode:
        raise RunnerError(f"exit {done.returncode}: {' '.join(done.stderr.split())[:300]}")
    return done.stdout


def clean_copy(repo: Path, dest: Path) -> Path:
    """A copy of `repo` at `dest` without the KB: no top-level KB_TRACES, no kb blocks in KB_BLOCK_FILES."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        return KB_TRACES & set(names) if Path(directory) == repo else set()

    shutil.copytree(repo, dest, ignore=lambda d, names: ignore(d, names) | ({".git"} & set(names) if Path(d) == repo else set()),
                    symlinks=True)
    for name in KB_BLOCK_FILES:
        path = dest / name
        if path.is_file() and not path.is_symlink():
            path.write_text(remove_block(path.read_text(encoding="utf-8"), "kb", "html"), encoding="utf-8")
    _fresh_git(dest)
    return dest


def _fresh_git(dest: Path) -> None:
    """A new one-commit repository, so git history/status/diff in the copy can't reveal the removed KB
    (agents such as codex expect a git repository)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "kb-eval", "GIT_AUTHOR_EMAIL": "kb-eval@example.invalid",
           "GIT_COMMITTER_NAME": "kb-eval", "GIT_COMMITTER_EMAIL": "kb-eval@example.invalid"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "eval copy"]):
        subprocess.run(["git", *args], cwd=dest, env=env, check=True, capture_output=True)


def run_eval(questions: list[dict], agent: str, with_kb: bool, repo: Path, runner: Runner = subprocess_runner) -> dict:
    """Score every question; a failed run is recorded as an error (counted wrong, not cited) and the rest continue."""
    scratch = None
    cwd = repo
    if not with_kb:
        scratch = Path(tempfile.mkdtemp(prefix="kb-eval-"))
        cwd = clean_copy(Path(repo), scratch / "repo")
    try:
        results = []
        for q in questions:
            error = None
            try:
                out = runner(build_command(agent, q["question"], with_kb), cwd)
                if failed := result_error(agent, out):
                    raise RunnerError(failed)
                answer = extract_answer(agent, out)
            except RunnerError as e:
                answer, error = "", str(e)
            entry = {"id": q["id"], "wrong": is_wrong(q, answer) or error is not None, "cited": is_cited(q, answer), "answer": answer}
            if error:
                entry["error"] = error
            results.append(entry)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    n = len(results)
    return {
        "questions": n,
        "errors": sum("error" in r for r in results),
        "wrong_fact_rate": sum(r["wrong"] for r in results) / n,
        "citation_rate": sum(r["cited"] for r in results) / n,
        "results": results,
    }


def refusal(repo: Path) -> str | None:
    """Why the eval must not run in `repo`, failing closed: without a config and registered sources nothing
    says the documents were checked for secrets, and with an unmasked secret the agent would read it."""
    if not (repo / "kb.config.yaml").is_file() or not KbRepo.load(repo).sources:
        return "refusing to run: no kb.config.yaml or no registered sources; run kb init and kb source add first"
    if any(i.code == "source.secret_in_original" for i in run_check(repo).issues):
        return ("refusing to run: kb check reports source.secret_in_original; remove the secret from the original document "
                "and run `kb source sync` (demo step 4, docs/phases/phase1-demo.md)")
    return None


def record(path: Path, agent: str, mode: str, run: dict) -> None:
    data = json.loads(path.read_text()) if path.exists() else {"agent": agent, "runs": {}}
    data["runs"][mode] = run
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None, runner: Runner = subprocess_runner) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--with-kb", dest="with_kb", action="store_true", default=True)
    parser.add_argument("--without-kb", dest="with_kb", action="store_false")
    parser.add_argument("--repo", type=Path, default=Path("."), help="The repository the agent runs in.")
    parser.add_argument("--questions", type=Path, default=HERE / "questions.yaml")
    parser.add_argument("--results-dir", type=Path, default=HERE / "results")
    args = parser.parse_args(argv)
    if why := refusal(args.repo):
        print(why, file=sys.stderr)
        return 2
    questions = load_yaml_strict(args.questions.read_text())
    mode = "with-kb" if args.with_kb else "without-kb"
    run = run_eval(questions, args.agent, args.with_kb, args.repo, runner)
    out = args.results_dir / f"phase1-{args.agent}.json"
    record(out, args.agent, mode, run)
    print(f"{args.agent} {mode}: wrong-fact rate {run['wrong_fact_rate']:.2f}, citation rate {run['citation_rate']:.2f} -> {out}")
    if run["errors"]:
        print(f"{run['errors']} questions failed to run (see 'error' in {out})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
