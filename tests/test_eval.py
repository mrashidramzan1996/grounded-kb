import importlib.util
import json
from pathlib import Path

import pytest

from kb.yamlio import load_yaml_strict

EVAL = Path(__file__).parents[1] / "eval"
spec = importlib.util.spec_from_file_location("eval_run", EVAL / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


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


def test_commands_are_read_only_and_headless():
    claude = run.build_command("claude", "Q?", with_kb=True)
    assert claude[:2] == ["claude", "-p"] and "json" in claude and "Write" not in claude[-1] and "Edit" not in claude[-1]
    assert "Bash(kb context:*)" in claude[-1] and "Bash" not in run.build_command("claude", "Q?", with_kb=False)[-1]
    codex = run.build_command("codex", "Q?", with_kb=False)
    assert codex[:5] == ["codex", "exec", "--sandbox", "read-only", "--json"]
    assert "kb/" in codex[-1] and "Q?" in codex[-1]
    with pytest.raises(ValueError):
        run.build_command("gemini", "Q?", True)


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


def test_main_writes_results_per_agent_and_mode(tmp_path):
    fake = lambda cmd, cwd: json.dumps({"result": "REQ-F-002 no refund"})  # noqa: E731
    args = ["--agent", "claude", "--repo", str(tmp_path), "--results-dir", str(tmp_path / "out")]
    assert run.main([*args, "--with-kb"], fake) == 0
    assert run.main([*args, "--without-kb"], fake) == 0
    data = json.loads((tmp_path / "out/phase1-claude.json").read_text())
    assert data["agent"] == "claude" and set(data["runs"]) == {"with-kb", "without-kb"}
    assert data["runs"]["with-kb"]["questions"] == 20
