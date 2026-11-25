"""kb clarify: a person's answer recorded as a quotable source."""

import re

import pytest
from helpers import QUOTE, TODO_TRIAGE, commit, invoke, make_git_repo, ok, put, req_yaml, todo_ingested

from kb.clarify import CLARIFICATIONS, ClarifyError, clarify
from kb.config.load import load_config
from kb.records.repo import KbRepo

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'
ANSWER = "The checkout page must load in under 2 seconds for 95% of visits."


def repo(tmp_path, config=CONFIG):
    return make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/n.md": "# Seats\n\nA seat costs 5 EUR.\n"})


def do(root, topic="Checkout speed", text=ANSWER, by="@alice", date="2026-11-25"):
    return clarify(root, topic, text, by, date)


def test_clarify_creates_file_and_source(tmp_path):
    root = repo(tmp_path)
    sid, ref = do(root)
    assert (root / CLARIFICATIONS).read_text() == (
        "# Clarifications\n\nAnswers to open questions, recorded by the coding agent from the decision-maker's "
        "reply. Each section can be quoted as evidence.\n\n"
        f"## Checkout speed\n\nDecided by @alice on 2026-11-25.\n\n{ANSWER}\n"
    )
    assert sid == "SRC-clarifications"
    assert KbRepo.load(root).sources[sid].original == CLARIFICATIONS


def test_clarify_appends_and_keeps_old_segment_ids(tmp_path):
    root = repo(tmp_path)
    sid, first = do(root)
    _, second = do(root, topic="Seat price", text="A seat costs 5 EUR.", by="@bob/team")
    text = (root / CLARIFICATIONS).read_text()
    assert text.index("## Checkout speed") < text.index("## Seat price")
    assert "\n\n## Seat price\n\nDecided by @bob/team on 2026-11-25.\n\nA seat costs 5 EUR.\n" in text
    ids = [s.id for s in KbRepo.load(root).segments(sid)]
    assert first.split("#")[1] in ids and second.split("#")[1] in ids and first != second


def test_clarify_returns_ref_of_new_section(tmp_path):
    root = repo(tmp_path)
    do(root)
    sid, ref = do(root, topic="Seat price", text="A seat costs 5 EUR.")
    assert re.fullmatch(r"SRC-clarifications#seg-\d+", ref)
    seg = ref.split("#")[1]
    heading = {s.id: s.heading for s in KbRepo.load(root).segments(sid)}[seg]
    assert heading.endswith("Seat price")
    _, again = do(root, topic="Seat price", text="Another.")  # same topic twice: the newest one
    assert again != ref
    assert {s.id: s.heading for s in KbRepo.load(root).segments(sid)}[again.split("#")[1]].endswith("Seat price")


@pytest.mark.parametrize("kwargs", [
    {"topic": ""}, {"topic": "  "}, {"text": ""}, {"text": " \t "},
    {"topic": "a\nb"}, {"text": "a\nb"}, {"text": "a\n\n## b"}, {"topic": "# Big"}, {"text": "# Big"},
    {"text": "use ```code``` here"}, {"topic": "a````b"}, {"text": "a\x07b"}, {"text": "a\x85b"},
    {"text": "a b"}, {"topic": "a b"},
])
def test_clarify_refuses_headings_and_blank_lines(tmp_path, kwargs):
    root = repo(tmp_path)
    with pytest.raises(ClarifyError):
        do(root, **kwargs)
    assert not (root / CLARIFICATIONS).exists()


@pytest.mark.parametrize("kwargs", [
    {"by": "alice"}, {"by": "@"}, {"by": "@a b"}, {"by": "@a/b/c"}, {"by": "@a\n"},
    {"date": "25-11-2026"}, {"date": "2026-11-25 "}, {"date": "2026-1-5"}, {"date": "today"},
])
def test_clarify_refuses_bad_handle_and_date(tmp_path, kwargs):
    root = repo(tmp_path)
    with pytest.raises(ClarifyError):
        do(root, **kwargs)
    assert not (root / CLARIFICATIONS).exists()


def test_clarify_refuses_symlinks(tmp_path):
    root = repo(tmp_path)
    (root / "other.md").write_text("x\n")
    (root / CLARIFICATIONS).symlink_to("../other.md")
    with pytest.raises(ClarifyError, match="symlink"):
        do(root)
    assert (root / "other.md").read_text() == "x\n"


def test_clarify_outside_sources_exits_2(tmp_path):
    root = repo(tmp_path, 'version: 1\nsources:\n  - paths: ["notes/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n')
    r = invoke(root, "clarify", "--topic", "T", "--text", "x", "--by", "@a", "--date", "2026-11-25")
    assert r.exit_code == 2
    assert "add docs/** (or docs/clarifications.md) to config.sources" in r.output
    assert not (root / CLARIFICATIONS).exists()
    assert invoke(root, "clarify", "--topic", "T", "--text", "x", "--by", "nope", "--date", "2026-11-25").exit_code == 2


def test_clarify_cli_prints_ref_and_defaults_date(tmp_path):
    root = repo(tmp_path)
    r = ok(root, "clarify", "--topic", "Speed", "--text", ANSWER, "--by", "@alice")
    assert r.stdout.startswith("recorded in docs/clarifications.md; quote it as SRC-clarifications#seg-")
    assert re.search(r"Decided by @alice on \d{4}-\d{2}-\d{2}\.", (root / CLARIFICATIONS).read_text())


def test_clarified_target_clears_vague_via_redraft(tmp_path):
    root = todo_ingested(tmp_path)
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", flags=("vague",)))
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001, REQ-NF-001]\n")
    commit(root, "drafts")
    ok(root, "clarify", "--topic", "Checkout speed", "--text", ANSWER, "--by", "@alice", "--date", "2026-11-25")
    ref = [s.id for s in KbRepo.load(root).segments("SRC-clarifications")][-1]
    ok(root, "redraft", "REQ-NF-001", "--new")
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", ANSWER).replace("SRC-n#seg-01", f"SRC-clarifications#{ref}"))
    put(root, "kb/sources/SRC-clarifications/triage.yaml", f"segments:\n  {ref}: [REQ-NF-001]\n")
    r = invoke(root, "redraft", "--report", "--strict")
    assert r.exit_code == 0, r.output


def test_clarify_cli_joins_wrapped_text(tmp_path):
    root = repo(tmp_path)
    ok(root, "clarify", "--topic", "Speed", "--text", "Under 2 seconds\nfor most\n  visits.", "--by", "@alice", "--date", "2026-11-25")
    assert "\n\nUnder 2 seconds for most visits.\n" in (root / CLARIFICATIONS).read_text()
