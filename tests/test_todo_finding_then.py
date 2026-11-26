"""The `then` text of an evaluation finding's options: glossary terms are not redrafted, and every edit is decided at once."""

from helpers import commit, put
from test_todo_eval import drafted

from kb.eval.decisions import SummaryFinding
from kb.records.repo import KbRepo
from kb.todo import _finding_ask

TERM = ("id: TERM-seat\nname: seat\nkind: concept\ndefinition: A paid place.\nevidence:\n  quotes:\n"
        "  - source: SRC-n#seg-01\n    text: A seat costs 5 EUR.\n")
IMMEDIATELY = "edit, then decide this finding immediately, before editing the next record"


def finding(kind: str, canonical: str, fid: str = "F-0123456789") -> SummaryFinding:
    return SummaryFinding(id=fid, canonical=canonical, kind=kind, ref="SRC-n#seg-01", text="A seat costs 5 EUR.",
                          severity="high", supporters=2, related=[], note="the definition is off", kind_hint=None, ends=[])


def test_a_term_finding_does_not_send_the_agent_to_kb_redraft(tmp_path):
    root = drafted(tmp_path)
    put(root, "kb/glossary/TERM-seat.yaml", TERM)
    commit(root, "term")
    ask = _finding_ask(finding("unfaithful", "R:TERM-seat"), KbRepo.load(root))
    fix = next(o for o in ask.options if o.key == "fix")
    assert "kb redraft" not in fix.then
    assert "kb/glossary/TERM-seat.yaml" in fix.then and "quote containing the name" in fix.then
    assert "`kb ingest --report`" in fix.then and "kb eval decide F-0123456789 --as fixed" in fix.then


def test_a_requirement_finding_still_uses_redraft_and_says_decide_at_once(tmp_path):
    root = drafted(tmp_path)
    ask = _finding_ask(finding("unfaithful", "R:REQ-F-001"), KbRepo.load(root))
    fix = next(o for o in ask.options if o.key == "fix")
    assert "`kb redraft REQ-F-001`" in fix.then and IMMEDIATELY in fix.then


def test_every_editing_option_says_decide_this_finding_at_once(tmp_path):
    root = drafted(tmp_path)
    repo = KbRepo.load(root)
    for f, keys in ((finding("omission", "S:abc"), ("add", "product")), (finding("split", "R:REQ-F-001"), ("fix",))):
        ask = _finding_ask(f, repo)
        for key in keys:
            assert IMMEDIATELY in next(o for o in ask.options if o.key == key).then, (f.kind, key)
