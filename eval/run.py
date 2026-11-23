"""Baseline eval: ask a coding agent the questions in questions.yaml, headless and read-only, and score it.

Run by hand, never in CI (it calls a real agent):

    uv run python eval/run.py --agent claude --with-kb  --repo /path/to/acme-billing
    uv run python eval/run.py --agent claude --without-kb --repo /path/to/acme-billing

--with-kb tells the agent to use `kb context` / `kb trace` and to cite record ids; --without-kb tells it to answer from
the documents under docs/ and not to read kb/. Both modes run on the same checkout. Results are merged into
eval/results/phase1-<agent>.json, one entry per mode:
  wrong_fact_rate: answers missing an expected fact or holding a forbidden one / questions
  citation_rate:   answers naming a record id or source document from `cites` / questions
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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


def build_command(agent: str, question: str, with_kb: bool) -> list[str]:
    prompt = PROMPT[with_kb].format(question=question)
    if agent == "claude":
        tools = "Read,Grep,Glob" + (",Bash(kb context:*),Bash(kb trace:*)" if with_kb else "")
        return ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", tools]
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


def is_wrong(question: dict, answer: str) -> bool:
    low = answer.lower()
    missing = any(not any(alt.lower() in low for alt in group) for group in question["expect"])
    return missing or any(f.lower() in low for f in question.get("forbid", []))


def is_cited(question: dict, answer: str) -> bool:
    return any(c in answer for c in question["cites"])


def subprocess_runner(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=600).stdout


def run_eval(questions: list[dict], agent: str, with_kb: bool, repo: Path, runner: Runner = subprocess_runner) -> dict:
    results = []
    for q in questions:
        answer = extract_answer(agent, runner(build_command(agent, q["question"], with_kb), repo))
        results.append({"id": q["id"], "wrong": is_wrong(q, answer), "cited": is_cited(q, answer), "answer": answer})
    n = len(results)
    return {
        "questions": n,
        "wrong_fact_rate": sum(r["wrong"] for r in results) / n,
        "citation_rate": sum(r["cited"] for r in results) / n,
        "results": results,
    }


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
    questions = load_yaml_strict(args.questions.read_text())
    mode = "with-kb" if args.with_kb else "without-kb"
    run = run_eval(questions, args.agent, args.with_kb, args.repo, runner)
    out = args.results_dir / f"phase1-{args.agent}.json"
    record(out, args.agent, mode, run)
    print(f"{args.agent} {mode}: wrong-fact rate {run['wrong_fact_rate']:.2f}, citation rate {run['citation_rate']:.2f} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
