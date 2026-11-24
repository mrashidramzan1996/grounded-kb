import importlib.util
import json
from pathlib import Path

import pytest

from kb.yamlio import load_yaml_strict

EVAL = Path(__file__).parents[1] / "eval"
spec = importlib.util.spec_from_file_location("eval_run", EVAL / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
staleness_spec = importlib.util.spec_from_file_location("eval_staleness", EVAL / "staleness.py")
staleness = importlib.util.module_from_spec(staleness_spec)
staleness_spec.loader.exec_module(staleness)


def questions():
    return load_yaml_strict((EVAL / "questions.yaml").read_text())


def test_questions_file_is_well_formed():
    qs = questions()
    assert len(qs) == 20 and len({q["id"] for q in qs}) == 20
    for q in qs:
        assert set(q) <= {"id", "question", "expect", "forbid", "cites"} and q["question"].endswith("?")
        assert q["expect"] and all(isinstance(g, list) and g for g in q["expect"]) and q["cites"]
    texts = " ".join(q["question"] for q in qs)
    assert "refund" in texts and "How long is billing data kept?" in texts


def test_expected_record_ids_exist_in_the_drafted_kb(fixtures):
    drafts = fixtures / "drafts/acme-billing/kb"
    existing = {p.stem for p in drafts.rglob("*") if p.is_file()}
    cited = {c for q in questions() for c in q["cites"] if c.split("-")[0] in {"REQ", "ADR", "INV"}}
    assert cited and cited <= existing


def flag(cmd, name):
    """The values following --name up to the next flag."""
    i = cmd.index(name) + 1
    out = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        out.append(cmd[i])
        i += 1
    return out


def test_claude_command_is_sandboxed_with_kb():
    cmd = run.build_command("claude", "Q?", with_kb=True)
    assert cmd[:2] == ["claude", "-p"] and flag(cmd, "--output-format") == ["json"]
    assert flag(cmd, "--permission-mode") == ["dontAsk"] and "--strict-mcp-config" in cmd and "--mcp-config" not in cmd
    assert flag(cmd, "--tools") == ["Read,Grep,Glob,Bash"]
    assert flag(cmd, "--allowedTools") == ["Read", "Grep", "Glob", "Bash(kb context:*)", "Bash(kb trace:*)"]
    assert flag(cmd, "--disallowedTools") == ["Edit", "Write", "NotebookEdit"]
    assert flag(cmd, "--setting-sources") == ["project"]


def test_claude_command_without_kb_has_no_bash():
    """kb/ is kept out by running in a copy without it, not by deny rules."""
    cmd = run.build_command("claude", "Q?", with_kb=False)
    assert flag(cmd, "--tools") == ["Read,Grep,Glob"] and flag(cmd, "--allowedTools") == ["Read", "Grep", "Glob"]
    assert flag(cmd, "--disallowedTools") == ["Edit", "Write", "NotebookEdit", "Bash"]
    assert flag(cmd, "--permission-mode") == ["dontAsk"] and "--strict-mcp-config" in cmd
    assert flag(cmd, "--setting-sources") == ["project"]


def test_codex_command_and_unknown_agent():
    codex = run.build_command("codex", "Q?", with_kb=False)
    assert codex[:5] == ["codex", "exec", "--sandbox", "read-only", "--json"] and "Q?" in codex[-1]
    with pytest.raises(ValueError):
        run.build_command("gemini", "Q?", True)


def test_codex_without_kb_runs_in_a_copy_without_kb(md_repo_ingested):
    assert (md_repo_ingested / "kb").is_dir()
    seen = []

    def fake(cmd, cwd):
        seen.append((cwd, sorted(p.name for p in cwd.iterdir()), cwd.is_dir()))
        return json.dumps({"item": {"type": "agent_message", "text": "x"}})

    run.run_eval(questions()[:1], "codex", False, md_repo_ingested, fake)
    cwd, names, _ = seen[0]
    assert cwd != md_repo_ingested and "kb" not in names and "docs" in names and not cwd.exists()
    run.run_eval(questions()[:1], "codex", True, md_repo_ingested, lambda c, d: seen.append(d) or "")
    assert seen[-1] == md_repo_ingested


def test_extract_answer():
    assert run.extract_answer("claude", json.dumps({"result": "No refund."})) == "No refund."
    assert run.extract_answer("claude", "not json") == ""
    lines = "\n".join(json.dumps(e) for e in [
        {"type": "thread.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "No refund."}},
    ]) + "\nnoise\n"
    assert run.extract_answer("codex", lines) == "No refund."


def test_scoring():
    q = {"expect": [["no refund"], ["cycle"]], "forbid": ["yes"], "cites": ["REQ-F-002"]}
    assert not run.is_wrong(q, "No refund; active to end of cycle. REQ-F-002")
    assert run.is_wrong(q, "No refund.") and run.is_wrong(q, "No refund, yes, cycle")
    assert run.is_cited(q, "see REQ-F-002") and not run.is_cited(q, "see REQ-F-001")


def test_run_eval_with_a_fake_runner(tmp_path):
    seen = []

    def fake(cmd, cwd):
        seen.append((cmd, cwd))
        return json.dumps({"result": "No refund is given (REQ-F-002)." if "refund" in cmd[2] else "I do not know."})

    qs = [q for q in questions() if q["id"] in {"Q01", "Q04"}]
    result = run.run_eval(qs, "claude", True, tmp_path, fake)
    assert result["questions"] == 2 and result["wrong_fact_rate"] == 0.5 and result["citation_rate"] == 0.5
    assert [r["wrong"] for r in result["results"]] == [False, True]
    assert all(cwd == tmp_path for _, cwd in seen)


def test_main_writes_results_per_agent_and_mode(md_repo_ingested, tmp_path):
    fake = lambda cmd, cwd: json.dumps({"result": "REQ-F-002 no refund"})  # noqa: E731
    args = ["--agent", "claude", "--repo", str(md_repo_ingested), "--results-dir", str(tmp_path / "out")]
    assert run.main([*args, "--with-kb"], fake) == 0
    assert run.main([*args, "--without-kb"], fake) == 0
    data = json.loads((tmp_path / "out/phase1-claude.json").read_text())
    assert data["agent"] == "claude" and set(data["runs"]) == {"with-kb", "without-kb"}
    assert data["runs"]["with-kb"]["questions"] == 20


def test_scoring_is_whole_word():
    q19 = next(q for q in questions() if q["id"] == "Q19")
    assert run.is_wrong(q19, "I don't know. Note: not sure.")
    assert run.is_wrong(q19, "Yes, it can.")
    assert not run.is_wrong(q19, "No, it cannot: the plan minimum applies.")
    q13 = next(q for q in questions() if q["id"] == "Q13")
    assert run.is_wrong(q13, "I know nothing about it.") and run.is_wrong(q13, "Yes, two months free.")
    assert not run.is_wrong(q13, "The document does not mention such an offer.")


def test_failed_runs_are_recorded_and_the_rest_continue(tmp_path):
    def flaky(cmd, cwd):
        if "refund" in cmd[2]:
            raise run.RunnerError("TimeoutExpired: 600s")
        return json.dumps({"result": "7 years (REQ-NF-002)"})

    qs = [q for q in questions() if q["id"] in {"Q01", "Q04"}]
    result = run.run_eval(qs, "claude", True, tmp_path, flaky)
    assert result["errors"] == 1 and result["results"][0]["error"].startswith("TimeoutExpired")
    assert result["results"][0]["wrong"] and not result["results"][1]["wrong"]


def test_subprocess_runner_raises_on_failure(tmp_path):
    with pytest.raises(run.RunnerError):
        run.subprocess_runner(["definitely-not-a-binary-xyz"], tmp_path)
    with pytest.raises(run.RunnerError, match="exit 3"):
        run.subprocess_runner(["sh", "-c", "exit 3"], tmp_path)


def test_main_exits_1_with_partial_results_on_errors(md_repo_ingested, tmp_path):
    def flaky(cmd, cwd):
        if "?" in cmd[2] and "Question: Is a refund" in cmd[2]:
            raise run.RunnerError("boom")
        return "{}"

    args = ["--agent", "claude", "--repo", str(md_repo_ingested), "--results-dir", str(tmp_path / "out")]
    assert run.main(args, flaky) == 1
    assert json.loads((tmp_path / "out/phase1-claude.json").read_text())["runs"]["with-kb"]["errors"] == 1


def test_main_refuses_when_the_repo_holds_a_secret(acme_repo_ingested, tmp_path, capsys):
    args = ["--agent", "claude", "--repo", str(acme_repo_ingested), "--results-dir", str(tmp_path / "out")]
    assert run.main(args, lambda c, d: pytest.fail("agent must not run")) == 2
    assert "source.secret_in_original" in capsys.readouterr().err and not (tmp_path / "out").exists()


# --- I9: an uncontaminated without-kb baseline -------------------------------------------------

KB_BLOCK = "<!-- kb:begin kb -->\nUse kb context and cite REQ ids.\n<!-- kb:end kb -->\n"


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_without_kb_runs_in_a_copy_without_any_kb_trace(md_repo_ingested, agent):
    root = md_repo_ingested
    for rel, text in {
        "AGENTS.md": "Team notes.\n\n" + KB_BLOCK, "CLAUDE.md": KB_BLOCK, ".kb/cache/ingest/brief.md": "brief",
        ".claude/settings.json": "{}", ".codex/hooks.json": "{}", "docs/kb/keep.md": "nested kb dir stays",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    seen = []

    def fake(cmd, cwd):
        names = {p.relative_to(cwd).as_posix() for p in cwd.rglob("*") if ".git" not in p.relative_to(cwd).parts}
        seen.append((cwd, names, (cwd / "AGENTS.md").read_text(), (cwd / "CLAUDE.md").read_text()))
        return json.dumps({"result": "x"}) if agent == "claude" else json.dumps({"item": {"type": "agent_message", "text": "x"}})

    run.run_eval(questions()[:1], agent, False, root, fake)
    cwd, names, agents_md, claude_md = seen[0]
    assert cwd != root and not cwd.exists()
    assert not {n for n in names if n.split("/")[0] in {"kb", ".kb", ".claude", ".codex"}}
    assert "docs/kb/keep.md" in names and "docs/n.md" in names
    assert agents_md == "Team notes.\n" and "kb:begin" not in claude_md
    run.run_eval(questions()[:1], agent, True, root, lambda c, d: seen.append(d) or "")
    assert seen[-1] == root


def test_claude_is_error_result_is_an_error(tmp_path):
    fake = lambda cmd, cwd: json.dumps({"is_error": True, "result": "Credit balance is too low"})  # noqa: E731
    result = run.run_eval(questions()[:1], "claude", True, tmp_path, fake)
    (entry,) = result["results"]
    assert result["errors"] == 1 and entry["wrong"] and "Credit balance" in entry["error"]


def test_secret_check_fails_closed(tmp_path, capsys):
    from helpers import make_git_repo

    bare = make_git_repo(None, tmp_path, extra={"kb.config.yaml": 'version: 1\nsources:\n  - paths: ["docs/**"]\n'})
    for repo in (tmp_path / "nothing", bare):
        repo.mkdir(exist_ok=True)
        args = ["--agent", "claude", "--repo", str(repo), "--results-dir", str(tmp_path / "out")]
        assert run.main(args, lambda c, d: pytest.fail("agent must not run")) == 2
        assert "run kb init and kb source add first" in capsys.readouterr().err


def test_without_kb_copy_has_no_kb_in_git(md_repo_ingested):
    """The copy gets a fresh repository: git history/status/diff can't reveal the removed kb/."""
    import subprocess

    root = md_repo_ingested
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "--allow-empty", "-qm", "kb"], cwd=root, check=True)
    seen = []

    def fake(cmd, cwd):
        git = lambda *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout
        seen.append((git("status", "--porcelain"), git("log", "--all", "--name-only", "--format="), git("diff", "HEAD")))
        return json.dumps({"item": {"type": "agent_message", "text": "x"}})

    run.run_eval(questions()[:1], "codex", False, root, fake)
    status, history, diff = seen[0]
    assert status == "" and diff == ""
    assert history.strip() and not any(line.startswith(("kb/", ".kb/")) for line in history.splitlines())


def test_staleness_changes_cover_the_seeded_kinds():
    from collections import Counter

    kinds = Counter(c.kind for c in staleness.CHANGES)
    assert kinds == {"refactor": 5, "format": 3, "rename": 3, "deleted_test": 3, "segment_reword": 3, "quote_removal": 2, "unrelated": 1}
    assert len({c.id for c in staleness.CHANGES}) == 20


def test_staleness_eval_runs_and_writes_results(tmp_path, monkeypatch):
    import os
    import sys

    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}:{os.environ['PATH']}")  # linked tests find pytest
    out = tmp_path / "staleness.json"
    assert staleness.main(["--out", str(out)]) == 0
    text = out.read_text()
    data = json.loads(text)
    assert data["changes"] == 20 and len(data["per_change"]) == 20
    assert 0 <= data["precision"] <= 1 and 0 <= data["recall"] <= 1
    assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert str(tmp_path) not in text and "kb-staleness-" not in text
    for c in data["per_change"]:
        assert set(c["expected"]) == set(c["predicted"]) and set(c["predicted"].values()) <= set(staleness.LABELS)


def test_staleness_score_counts_matching_labels_per_id():
    def change(expected, predicted):
        return {"expected": expected, "predicted": predicted}

    per_change = [
        change({"A": "broken", "B": "none"}, {"A": "broken", "B": "changed"}),  # 1 tp, 1 fp
        change({"A": "stale", "B": "none"}, {"A": "broken", "B": "none"}),  # wrong label: 1 fp, 1 fn
        change({"A": "changed", "B": "none"}, {"A": "none", "B": "none"}),  # missed: 1 fn
    ]
    assert staleness.score(per_change) == (1 / 3, 1 / 3)
    assert staleness.score([change({"A": "none"}, {"A": "none"})]) == (1.0, 1.0)


def test_staleness_changed_label_only_on_verified():
    """A-4: `changed` means a changed fingerprint whose tests still pass; on an unverified claim it is not a label."""
    from types import SimpleNamespace

    def v(status, *notes):
        return SimpleNamespace(status=status, notes=notes)

    assert staleness.label_of(v("verified", "changed")) == "changed"
    assert staleness.label_of(v("unverified", "changed", "flaky")) == "none"
    assert staleness.label_of(v("broken", "changed")) == "broken" and staleness.label_of(v("stale")) == "stale"


def test_staleness_expected_ids_are_always_scored(tmp_path, monkeypatch):
    """An expected id that is neither a fixture id nor in the verdicts still counts (as a miss), never silently drops."""
    from types import SimpleNamespace

    monkeypatch.setattr(staleness, "run_verify", lambda *a, **k: SimpleNamespace(verdicts=[]))
    change = staleness.Change("x", "unrelated", staleness.write("README.md", "x\n"), {"FEAT-999.C1": "broken"})
    out = staleness.run_change(change, tmp_path, staleness.FIXTURE, ["FEAT-001.C1"])
    assert out["expected"] == {"FEAT-001.C1": "none", "FEAT-999.C1": "broken"}
    assert out["predicted"] == {"FEAT-001.C1": "none", "FEAT-999.C1": "none"} and not out["correct"]
