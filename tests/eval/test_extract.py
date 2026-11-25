"""`kb eval extract`: the blind second-extraction brief, and run B validated as untrusted input."""

import os
from pathlib import Path

from helpers import git, invoke, make_git_repo, stored_repo

from kb.config.load import load_config
from kb.eval.extract import (
    EVAL_BASE,
    EXTRACT_BRIEF,
    RUN_B,
    build_extract_brief,
    load_run_b,
)
from kb.gitfiles import head_commit
from kb.records.repo import KbRepo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - {id: MOD-billing, owner: "@alice"}\n'
SPEC = (
    "# Billing\n\nA seat costs 5 EUR per month. Refunds are never given for a removed seat.\n\n"
    "# Accounts\n\nAn account is a customer of Acme. Each account owns its invoices.\n"
)
LIVE_STATEMENT = "Seat pricing is a flat monthly fee of 5 EUR."
LIVE_REQ = f"""\
id: REQ-F-001
statement: {LIVE_STATEMENT}
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: A seat costs 5 EUR per month.
modules: [MOD-billing]
modality: must
actor: the billing system
"""
HEADER = (
    "Work in a fresh session or subagent. Do not read kb/requirements, kb/decisions, kb/invariants, kb/exclusions or "
    "kb/features; extract from the segments below only. Work segment by segment, bottom-up. Also record entities as "
    "TERM records with relations."
)


def ingested(tmp_path: Path, *, records: bool = True) -> Path:
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/spec.md": SPEC})
    assert invoke(root, "ingest").exit_code == 0
    if records:
        (root / "kb/requirements").mkdir(parents=True)
        (root / "kb/requirements/REQ-F-001.yaml").write_text(LIVE_REQ)
    git(root, "add", "-A")
    git(root, "commit", "-m", "ingest")
    return root


def flat(text: str) -> str:
    return " ".join(text.split())


def test_extract_brief_is_blind_and_complete(tmp_path):
    root = ingested(tmp_path)
    repo = KbRepo.load(root)
    brief = build_extract_brief(root, load_config(root / "kb.config.yaml"))
    refs = [f"{src}#{seg.id}" for src in repo.sources for seg in repo.segments(src)]
    assert len(refs) == 2 and all(f"### {ref} · page" in brief for ref in refs)
    assert HEADER in flat(brief)
    # blind: no live statement and no live record id; run B numbers past the live ids, with the B- prefix
    assert LIVE_STATEMENT not in brief and "REQ-F-001" not in brief
    assert "B-REQ-F-002" in brief and "B-REQ-NF-001" in brief and "B-EXCL-001" in brief
    # the ingest rules, reused, with run B's own drafted_by and destination
    assert "Quote word for word from the cited segment." in brief and "relations" in brief
    assert 'drafted_by: "agent:<your name> via kb eval extract"' in brief and "via kb ingest brief" not in brief
    assert f"{RUN_B}/requirements/B-REQ-F-" in brief and f"{RUN_B}/glossary/B-TERM-" in brief
    assert "Never write, edit or delete anything under `kb/`" in brief
    # the examples are run B files: B- ids, run-b paths, no triage, feature or component
    assert f"#### {RUN_B}/requirements/B-REQ-F-034.yaml" in brief and "id: B-REQ-F-034" in brief
    assert "target: B-TERM-subscription" in brief and "triage.yaml\n" not in brief and "FEAT-014" not in brief
    assert "#### kb/" not in brief
    assert build_extract_brief(root, load_config(root / "kb.config.yaml")) == brief  # deterministic


def test_ingest_brief_unchanged_by_shared_rules(tmp_path):
    root = ingested(tmp_path, records=False)
    brief = (root / ".kb/cache/ingest/brief.md").read_text()
    assert 'drafted_by: "agent:<your name> via kb ingest brief v1"' in brief and "8. Set" in brief
    assert "1. Draft one fact per requirement" in brief and "#### kb/requirements/REQ-F-034.yaml" in brief
    assert "$" not in brief.split("## Segments")[0]


def test_eval_extract_writes_brief_and_base_and_clears_run_b(tmp_path):
    root = ingested(tmp_path)
    stale = root / RUN_B / "requirements/B-REQ-F-009.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("old run\n")
    before = {p: p.read_bytes() for p in (root / "kb").rglob("*") if p.is_file()}
    r = invoke(root, "eval", "extract")
    assert r.exit_code == 0, r.output
    assert EXTRACT_BRIEF in r.stdout
    assert (root / EXTRACT_BRIEF).read_text() == build_extract_brief(root, load_config(root / "kb.config.yaml"))
    assert (root / EVAL_BASE).read_text().strip() == head_commit(root)
    assert (root / RUN_B).is_dir() and list((root / RUN_B).iterdir()) == []
    assert {p: p.read_bytes() for p in (root / "kb").rglob("*") if p.is_file()} == before  # kb/ untouched


def test_eval_extract_exits_2_when_nothing_is_ingested(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/spec.md": SPEC})
    r = invoke(root, "eval", "extract")
    assert r.exit_code == 2
    assert "kb ingest" in r.stderr
    assert not (root / EXTRACT_BRIEF).exists()


def test_eval_extract_refuses_a_symlinked_run_b(tmp_path):
    root = ingested(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    (root / RUN_B).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, root / RUN_B)
    r = invoke(root, "eval", "extract")
    assert r.exit_code == 2 and "symlink" in r.stderr
    assert (outside / "keep.txt").read_text() == "keep\n"


# --- run B ---------------------------------------------------------------------------------------------------------

def b_req(rid: str, text: str, *, seg: str = "seg-01", src: str = "SRC-spec", statement: str | None = None) -> str:
    return (f"id: {rid}\nstatement: {statement or text}\nevidence:\n  quotes:\n  - source: {src}#{seg}\n    text: {text}\n"
            "modules: [MOD-billing]\nmodality: must\nactor: the billing system\ndrafted_by: agent:b via kb eval extract\n")


def b_term(tid: str, name: str, quote: str, *, aliases=(), seg: str = "seg-02", relations=()) -> str:
    out = f"id: {tid}\nname: {name}\n"
    out += "aliases:\n" + "".join(f"- {a}\n" for a in aliases) if aliases else ""
    out += f"definition: The {name}.\nkind: data\nevidence:\n  quotes:\n  - source: SRC-spec#{seg}\n    text: {quote}\n"
    if relations:
        out += "relations:\n" + "".join(
            f"- type: {t}\n  target: {target}\n  quote:\n    source: SRC-spec#{seg}\n    text: {text}\n" for t, target, text in relations
        )
    return out


def run_b(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, text in files.items():
        p = root / RUN_B / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text if isinstance(text, bytes) else text.encode())


def kb_repo(*live: tuple[str, str]) -> KbRepo:
    repo = stored_repo(SPEC, dict(live))
    assert repo.errors == [], repo.errors
    return repo


ACCOUNT = b_term("B-TERM-account", "account", "An account is a customer of Acme.",
                 relations=[("owns", "B-TERM-invoice", "Each account owns its invoices.")])
INVOICE = b_term("B-TERM-invoice", "invoice", "Each account owns its invoices.", aliases=["invoices"])


def test_run_b_items_are_validated_not_trusted(tmp_path):
    run_b(tmp_path, {
        "requirements/B-REQ-F-002.yaml": b_req("B-REQ-F-002", "A seat costs 5 EUR per month."),
        "requirements/B-REQ-F-003.yaml": b_req("B-REQ-F-003", "A seat costs 5 EUR per month.", seg="seg-09"),
        "requirements/B-REQ-F-004.yaml": b_req("B-REQ-F-004", "A seat costs 6 EUR per month."),
        "requirements/REQ-F-005.yaml": b_req("REQ-F-005", "Refunds are never given for a removed seat."),
        "glossary/B-TERM-account.yaml": ACCOUNT,
        "glossary/B-TERM-invoice.yaml": INVOICE,
    })
    items, issues = load_run_b(tmp_path, kb_repo())
    assert [i.id for i in items] == ["B-REQ-F-002", "B-TERM-account", "B-TERM-invoice"]
    assert [i.code for i in issues] == ["eval.run_b_invalid"] * 3
    by_path = {i.path: i.message for i in issues}
    assert set(by_path) == {f"{RUN_B}/requirements/B-REQ-F-003.yaml", f"{RUN_B}/requirements/B-REQ-F-004.yaml",
                            f"{RUN_B}/requirements/REQ-F-005.yaml"}
    assert "unknown segment SRC-spec#seg-09" in by_path[f"{RUN_B}/requirements/B-REQ-F-003.yaml"]
    assert "quote not found in SRC-spec#seg-01" in by_path[f"{RUN_B}/requirements/B-REQ-F-004.yaml"]
    assert "B- prefix" in by_path[f"{RUN_B}/requirements/REQ-F-005.yaml"]
    assert all("requirements/" in m for m in by_path.values())  # each issue names its file
    (req,) = [i for i in items if i.kind == "requirement"]
    text = kb_repo().source_text("SRC-spec")
    ((src, a, b),) = req.spans
    assert src == "SRC-spec" and text[a:b] == "A seat costs 5 EUR per month."
    account = next(i for i in items if i.id == "B-TERM-account")
    assert account.kind == "entity" and account.names == ("account",)
    assert [(r.type, r.target) for r in account.relations] == [("owns", "B-TERM-invoice")]


def test_run_b_id_that_is_a_live_kb_id_is_invalid(tmp_path):
    run_b(tmp_path, {
        "requirements/B-REQ-F-001.yaml": b_req("B-REQ-F-001", "A seat costs 5 EUR per month."),
        "requirements/B-REQ-F-002.yaml": b_req("B-REQ-F-002", "Refunds are never given for a removed seat."),
    })
    items, issues = load_run_b(tmp_path, kb_repo(("kb/requirements/REQ-F-001.yaml", LIVE_REQ)))
    assert [i.id for i in items] == ["B-REQ-F-002"]
    (issue,) = issues
    assert issue.code == "eval.run_b_invalid" and issue.path == f"{RUN_B}/requirements/B-REQ-F-001.yaml"
    assert "live kb record" in issue.message


def test_run_b_numbers_sources_and_relations_are_checked(tmp_path):
    run_b(tmp_path, {
        # a number the quote does not have
        "requirements/B-REQ-F-002.yaml": b_req("B-REQ-F-002", "A seat costs 5 EUR per month.", statement="A seat costs 7 EUR."),
        "requirements/B-REQ-F-003.yaml": b_req("B-REQ-F-003", "A seat costs 5 EUR per month.", src="SRC-other"),
        "requirements/B-REQ-F-004.yaml": b_req("B-REQ-F-004", "seat"),  # too short
        # the relation quote names only one end; the term itself is fine
        "glossary/B-TERM-account.yaml": b_term("B-TERM-account", "account", "An account is a customer of Acme.",
                                               relations=[("owns", "B-TERM-invoice", "An account is a customer of Acme."),
                                                          ("uses", "B-TERM-nothing", "Each account owns its invoices.")]),
        "glossary/B-TERM-invoice.yaml": INVOICE,
        # the name is not in the quote
        "glossary/B-TERM-seat.yaml": b_term("B-TERM-seat", "seat", "An account is a customer of Acme."),
    })
    items, issues = load_run_b(tmp_path, kb_repo())
    assert [i.id for i in items] == ["B-TERM-account", "B-TERM-invoice"]
    assert next(i for i in items if i.id == "B-TERM-account").relations == ()
    assert {i.code for i in issues} == {"eval.run_b_invalid"}
    msgs = sorted(i.message for i in issues)
    assert len(msgs) == 6
    joined = "\n".join(msgs)
    for needle in ("numbers not in any found quote: 7", "unknown source SRC-other", "shorter than 12 characters",
                   "does not name both", "B-TERM-nothing is not a run B term", "neither the name nor an alias"):
        assert needle in joined, needle


def test_run_b_garbage_is_reported_not_raised(tmp_path):
    outside = tmp_path / "secret.yaml"
    outside.write_text(b_req("B-REQ-F-009", "A seat costs 5 EUR per month."))
    run_b(tmp_path, {
        "requirements/B-REQ-F-002.yaml": "id: [unclosed\n",
        "requirements/B-REQ-F-003.yaml": b"\xff\xfe",
        "requirements/B-REQ-F-004.yaml": b_req("B-REQ-F-005", "A seat costs 5 EUR per month."),  # name and id differ
        "decisions/B-ADR-0001.md": "---\nid: B-ADR-0001\ntitle: Seats\ngoverns: [MOD-billing]\n---\nNo quote.\n",
        "features/B-FEAT-001.md": "---\nid: B-FEAT-001\ntitle: x\nmodule: MOD-billing\n---\n",
        "notes.txt": "hello",
        ".DS_Store": "ignored",
    })
    os.symlink(outside, tmp_path / RUN_B / "requirements/B-REQ-F-009.yaml")
    items, issues = load_run_b(tmp_path, kb_repo())
    assert items == []
    assert len(issues) == 7 and {i.code for i in issues} == {"eval.run_b_invalid"}
    msg = {i.path.rsplit("/", 2)[-2] + "/" + i.path.rsplit("/", 1)[-1]: i.message for i in issues}
    assert "symlink" in msg["requirements/B-REQ-F-009.yaml"]
    assert "quotes no source" in msg["decisions/B-ADR-0001.md"]
    assert "not a run B record file" in msg["features/B-FEAT-001.md"]


def test_missing_run_b_is_empty(tmp_path):
    assert load_run_b(tmp_path, kb_repo()) == ([], [])
