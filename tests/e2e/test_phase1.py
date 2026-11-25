"""Phase 1 acceptance: the whole docs-only story on a copy of the acme-billing fixture, CLI end to end."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import SECRET_KEY, apply_drafts, git, invoke, payload, remove_secret_paragraph, write
from kb.hooks import run_hook


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def check_json(repo: Path, *args: str):
    r = invoke(repo, "check", "--format", "json", *args)
    return r, json.loads(r.stdout)["issues"]


@pytest.fixture
def story(fixtures, tmp_path):
    from helpers import make_git_repo

    return make_git_repo(fixtures / "repos/acme-billing", tmp_path)


def test_phase1_docs_only_story(story, fixtures):
    repo = story

    # 1. kb init, set the approvers, commit
    assert invoke(repo, "init").exit_code == 0
    cfg = repo / "kb.config.yaml"
    text = cfg.read_text()
    assert text.count("[TODO]") == 2
    text = text.replace("intent: [TODO]", 'intent: ["@acme/product"]').replace("decisions: [TODO]", 'decisions: ["@acme/tech-leads"]')
    cfg.write_text(text)
    commit(repo, "kb init")

    # 2. kb ingest docs/: the brief covers all 3 documents; nothing unmasked is stored
    r = invoke(repo, "ingest", "docs/")
    assert r.exit_code == 0, r.output
    brief = (repo / ".kb/cache/ingest/brief.md").read_text()
    for stem in ("product-brief", "pricing-spec-v3", "architecture-notes"):
        assert f"SRC-{stem}" in brief
    stored = "\n".join(p.read_text() for p in (repo / "kb/sources").rglob("*") if p.is_file() and p.suffix in {".md", ".yaml", ".txt"})
    assert SECRET_KEY not in stored and "product-team@acme.example" not in stored
    assert {p.name for p in (repo / "kb/sources").iterdir()} == {"SRC-product-brief", "SRC-pricing-spec-v3", "SRC-architecture-notes"}
    commit(repo, "sources")

    # 3. simulated agent drafts, then the report
    #    (the drafts' config overlay would replace the init config, so the agent's proposed modules go in
    #    through `kb module add`, which writes the same entries)
    own_config = cfg.read_text()
    apply_drafts(repo, fixtures / "drafts/acme-billing")
    cfg.write_text(own_config)
    for mod, owner, risk in (("MOD-billing", "@acme/billing", "high"), ("MOD-sessions", "@acme/platform", "standard"), ("MOD-web", "@acme/web", "low")):
        assert invoke(repo, "module", "add", mod, "--owner", owner, "--risk", risk).exit_code == 0
    r = invoke(repo, "ingest", "--report")
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert "## Rejected (2)" in out and "REQ-F-005" in out and "quote.not_found" in out and "triage.not_quoted" in out
    assert "REQ-NF-001" in out and "requirement.vague" in out
    assert "## Untriaged segments (1)" in out and "5. Contact" in out
    assert "## Proposed modules (3)" in out
    for mod in ("MOD-billing", "MOD-sessions", "MOD-web"):
        assert mod in out
    assert invoke(repo, "ingest", "--report", "--strict").exit_code == 1

    # 4. remove REQ-F-005, triage the Contact segment; the planted key in the original still fails the strict check
    (repo / "kb/requirements/REQ-F-005.yaml").unlink()
    pricing = repo / "kb/sources/SRC-pricing-spec-v3/triage.yaml"  # seg-07 named the rejected requirement
    pricing.write_text(pricing.read_text().replace("seg-07: [REQ-F-005]", 'seg-07: "none: other: annual billing note, no requirement drafted"'))
    triage = repo / "kb/sources/SRC-product-brief/triage.yaml"
    triage.write_text(triage.read_text().rstrip("\n") + '\n  seg-06: "none: contact: details"\n')
    commit(repo, "accept drafts")
    r, issues = check_json(repo, "--strict")
    assert r.exit_code == 1 and "source.secret_in_original" in {i["code"] for i in issues if i["severity"] == "error"}
    #    fix the original, commit it together with `kb source sync`, then the strict check passes
    remove_secret_paragraph(repo / "docs/architecture-notes.docx")
    assert invoke(repo, "source", "sync").exit_code == 0
    commit(repo, "remove the example key from the architecture notes")
    r, issues = check_json(repo, "--strict")
    assert r.exit_code == 0, [i for i in issues if i["severity"] == "error"]
    assert not [i for i in issues if i["severity"] == "error"]

    # 5. baseline freeze creates the tag; nothing is built yet
    r = invoke(repo, "baseline", "freeze", "v1")
    assert r.exit_code == 0, r.output
    assert "kb-baseline-v1" in git(repo, "tag", "--list")
    commit(repo, "baseline v1")
    status = json.loads(invoke(repo, "status", "--format", "json").stdout)
    total = status["total"]["total"]
    assert total == 6 and status["total"]["built"] == 0
    assert f"0/{total} built" in invoke(repo, "status").stdout

    # 6. context for a module, and the evidence chain
    r = invoke(repo, "context", "--module", "MOD-billing")
    assert r.exit_code == 0, r.output
    assert "REQ-F-002" in r.stdout and "[REQ-F-002] not_built" in r.stdout and "ADR-0002" in r.stdout
    assert "No refund is given." in r.stdout and "integer cents" in r.stdout
    r = invoke(repo, "trace", "REQ-F-002")
    assert r.exit_code == 0, r.output
    assert "pricing-spec-v3.pdf" in r.stdout and "3.2 Removing seats" in r.stdout and "page 1" in r.stdout.lower()

    # 7. the agent builds seat removal
    write(repo, "services/billing/seats.py", "# r[impl REQ-F-002]\ndef remove_seat(seat):\n    return 0\n")
    write(repo, "tests/test_seats.py", "# r[verify REQ-F-002]\ndef test_no_refund():\n    assert True\n")
    commit(repo, "seat removal")
    status = json.loads(invoke(repo, "status", "--format", "json").stdout)
    assert status["total"]["built"] == 1 and status["total"]["tested"] == 1
    _, issues = check_json(repo)
    suggested = [i for i in issues if i["code"] == "module.paths_suggested"]
    assert suggested and "services/billing/" in suggested[0]["message"] and suggested[0]["record"] == "MOD-billing"

    # 8. accept the suggested paths: context by path now includes the requirement
    r = invoke(repo, "module", "paths", "MOD-billing", "--accept")
    assert r.exit_code == 0 and "services/billing/**" in r.stdout
    commit(repo, "module paths")
    r = invoke(repo, "context", "--paths", "services/billing/seats.py")
    assert r.exit_code == 0 and "REQ-F-002" in r.stdout

    # 9. the Claude Code stop hook blocks on a marker naming an unknown id, and passes once it is fixed
    stop = payload(fixtures, "claude_code/stop")
    assert run_hook(repo, "stop", "claude-code", stop) == ("", 0)
    write(repo, "services/billing/seats.py", "# r[impl REQ-F-999]\ndef remove_seat(seat):\n    return 0\n")
    out, code = run_hook(repo, "stop", "claude-code", stop)
    assert code == 0 and json.loads(out)["decision"] == "block" and "marker.unknown_id" in out
    write(repo, "services/billing/seats.py", "# r[impl REQ-F-002]\ndef remove_seat(seat):\n    return 0\n")
    assert run_hook(repo, "stop", "claude-code", stop) == ("", 0)


@pytest.mark.slow
def test_built_wheel_installs_in_a_throwaway_venv(tmp_path):
    root = Path(__file__).parents[2]
    dist = tmp_path / "dist"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist), str(root)], check=True, capture_output=True, text=True)
    (wheel,) = dist.glob("*.whl")
    venv = tmp_path / "v"
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True, text=True)
    subprocess.run(["uv", "pip", "install", "--python", str(venv), str(wheel)], check=True, capture_output=True, text=True)
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    out = subprocess.run([str(venv / bin_dir / "kb"), "version"], check=True, capture_output=True, text=True)
    import kb

    assert out.stdout.strip() == kb.__version__
