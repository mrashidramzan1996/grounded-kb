from helpers import git, invoke

from kb.gates.triage import triage_gate
from kb.ingest.report import ingest_report
from kb.records.models import NONE_CODES

TRIAGE = "kb/sources/SRC-pricing-spec-v3/triage.yaml"
VALID_TRIAGE = (
    "segments:\n  seg-01: 'none: heading: document title'\n  seg-02:\n  - REQ-F-003\n  seg-03:\n  - REQ-F-001\n  - REQ-F-002\n"
    "  seg-04:\n  - ADR-0002\n  seg-05:\n  - ADR-0003\n  - INV-001\n"
)


def with_triage(text: str):
    def mutate(files):
        files[TRIAGE] = text.encode()

    return mutate


def _codes(issues):
    return [i.code for i in issues]


def test_valid_has_no_untriaged(valid_ctx):
    assert triage_gate(valid_ctx) == []


def test_missing_triage_file_warns_for_every_segment(valid_ctx):
    valid_ctx.repo._triage.clear()
    issues = triage_gate(valid_ctx)
    assert len(issues) == len(valid_ctx.repo.segments("SRC-pricing-spec-v3"))
    assert {i.code for i in issues} == {"segment.untriaged"} and {i.severity for i in issues} == {"warning"}
    assert "`SRC-pricing-spec-v3#seg-01`" in issues[0].message


def test_triage_naming_non_quoting_record_is_error(valid_ctx_with):
    # FEAT-001's only requirement (REQ-F-002) quotes seg-03, not seg-05
    bad = triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("  - ADR-0003\n  - INV-001\n", "  - FEAT-001\n"))))
    assert _codes(bad) == ["triage.not_quoted"]
    (i,) = bad
    assert (i.path, i.record) == (TRIAGE, "SRC-pricing-spec-v3")
    assert i.message == "SRC-pricing-spec-v3#seg-05: triage names `FEAT-001` but none of them quotes this segment"
    # a warning in `kb check` (a 0.3 repo stays valid); an error in the ingest and redraft reports
    assert i.severity == "warning"
    # a feature whose requirement quotes the segment, a claim whose requirement does, and a record quoting it pass
    for named in ("  - FEAT-001\n", "  - REQ-F-002\n", "  - FEAT-001\n  - REQ-F-003\n"):
        ok = VALID_TRIAGE.replace("  - REQ-F-001\n  - REQ-F-002\n", named)
        assert triage_gate(valid_ctx_with(with_triage(ok))) == [], named
    several = triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("  - REQ-F-003\n", "  - REQ-F-001\n  - FEAT-001\n"))))
    assert [i.message for i in several] == [
        "SRC-pricing-spec-v3#seg-02: triage names `REQ-F-001`, `FEAT-001` but none of them quotes this segment"
    ]


def _set(path: str, old: bytes, new: bytes):
    def mutate(files):
        assert old in files[path]
        files[path] = files[path].replace(old, new)

    return mutate


REQ_3 = "kb/requirements/REQ-F-003.yaml"


def test_only_a_found_quote_of_a_live_record_counts(valid_ctx_with):
    """REQ-F-003 is seg-02's only record: a quote that is not found, too short, or on a rejected record quotes nothing."""
    extra = _set(REQ_3, b"evidence:\n  quotes:\n", b"evidence:\n  mode: any\n  quotes:\n  - source: SRC-pricing-spec-v3#seg-04\n"
                 b"    text: All amounts are stored and calculated in integer cents.\n")
    # mode any: the found quote now cites seg-04; the one citing seg-02 is invented
    moved = _set(REQ_3, b"evidence:\n  quotes:\n  - source: SRC-pricing-spec-v3#seg-02\n    text: Adding",
                 b"evidence:\n  mode: any\n  quotes:\n  - source: SRC-pricing-spec-v3#seg-02\n"
                 b"    text: Invented text that is not in the document.\n"
                 b"  - source: SRC-pricing-spec-v3#seg-04\n    text: All amounts are stored and calculated in integer cents.\n"
                 b"  - source: SRC-pricing-spec-v3#seg-04\n    text: Adding")
    rejected = _set(REQ_3, b"id: REQ-F-003\n", b"id: REQ-F-003\nstatus: rejected\n")
    for name, mutation in (("mode any, bogus quote cites seg-02", moved), ("rejected", rejected)):
        codes = _codes(triage_gate(valid_ctx_with(mutation)))
        assert codes == ["triage.not_quoted"], name
    assert triage_gate(valid_ctx_with(extra)) == []  # a found quote of seg-02 is still there


def test_term_without_evidence_and_component_ids_are_skipped(valid_ctx_with):
    # TERM-seat has no evidence: like a COMP id, it neither satisfies nor fails the check
    only = triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("  - REQ-F-003\n", "  - TERM-seat\n"))))
    assert only == []
    mixed = triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("  - REQ-F-003\n", "  - TERM-seat\n  - FEAT-001\n"))))
    assert [i.message for i in mixed] == ["SRC-pricing-spec-v3#seg-02: triage names `FEAT-001` but none of them quotes this segment"]


def test_term_with_evidence_is_checked(valid_ctx_with):
    """A term with evidence satisfies triage.not_quoted through a found quote in the segment, and fails it otherwise."""
    def seat(seg: str, text: str):
        return _set("kb/glossary/TERM-seat.yaml", b"definition:", f"kind: data\nevidence:\n  quotes:\n  - source: "
                    f"SRC-pricing-spec-v3#{seg}\n    text: {text}\ndefinition:".encode())

    only = with_triage(VALID_TRIAGE.replace("  - REQ-F-003\n", "  - TERM-seat\n"))
    quoted = seat("seg-02", "Adding a seat mid-cycle is charged pro-rata")
    assert triage_gate(valid_ctx_with(only, quoted)) == []
    elsewhere = seat("seg-04", "All amounts are stored and calculated in integer cents.")
    assert [i.message for i in triage_gate(valid_ctx_with(only, elsewhere))] == [
        "SRC-pricing-spec-v3#seg-02: triage names `TERM-seat` but none of them quotes this segment"]


def test_none_reason_codes(valid_ctx_with):
    def run(value):
        return triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("'none: heading: document title'", value))))

    assert run("'none: heading'") == []
    assert run("'none: heading: the title'") == []
    for code in NONE_CODES:
        if code != "out-of-scope":
            assert run(f"'none: {code}: why'") == [], code
    uncoded = run("'none: whatever'")
    assert _codes(uncoded) == ["triage.none_uncoded"] and uncoded[0].severity == "warning"
    assert uncoded[0].path == TRIAGE and uncoded[0].message.startswith("SRC-pricing-spec-v3#seg-01: ")
    assert _codes(run("'none: document title'")) == ["triage.none_uncoded"]
    scope = run("'none: out-of-scope: v2'")
    assert _codes(scope) == ["triage.use_exclusion"] and scope[0].severity == "warning"
    assert "record it as an EXCL record instead" in scope[0].message


def test_none_reason_text_is_escaped(valid_ctx_with):
    (i,) = triage_gate(valid_ctx_with(with_triage(VALID_TRIAGE.replace("'none: heading: document title'", "'none: x`y\n  z'"))))
    assert i.code == "triage.none_uncoded" and "\n" not in i.message and "``none: x`y z``" in i.message


def test_ingest_report_rejects_new_uncoded_none(md_repo_ingested):
    root = md_repo_ingested
    tri = root / "kb/sources/SRC-n/triage.yaml"
    tri.write_text("segments:\n  seg-01: 'none: price only'\n")
    r = ingest_report(root, "HEAD")
    assert [(i.code, i.path, i.severity) for i in r.rejected] == [("triage.none_uncoded", "kb/sources/SRC-n/triage.yaml", "error")]
    assert invoke(root, "ingest", "--report", "--strict").exit_code == 1
    tri.write_text("segments:\n  seg-01: 'none: other: price only'\n")
    r = ingest_report(root, "HEAD")
    assert r.rejected == []
    # a triage file written in this range shows all its warnings
    assert sorted(_codes(r.flagged)) == ["sentence.uncovered", "triage.none_has_rules"]
    # committed earlier (a 0.3 repo): only a warning in kb check, and not reported again
    tri.write_text("segments:\n  seg-01: 'none: price only'\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "old triage")
    assert ingest_report(root, "HEAD").rejected == []
    out = invoke(root, "check")
    assert out.exit_code == 0 and "triage.none_uncoded" in out.output


def test_ingest_report_rejects_triage_naming_non_quoting_record(md_repo_ingested):
    root = md_repo_ingested
    (root / "kb.config.yaml").write_text((root / "kb.config.yaml").read_text() + "modules:\n  - {id: MOD-a, owner: '@a'}\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "module")
    (root / "kb/features").mkdir(parents=True)
    (root / "kb/features/FEAT-001.md").write_text("---\nid: FEAT-001\ntitle: Seats\nmodule: MOD-a\n---\nSeats.\n")
    (root / "kb/sources/SRC-n/triage.yaml").write_text("segments:\n  seg-01: [FEAT-001]\n")
    r = ingest_report(root, "HEAD")
    assert [(i.code, i.severity) for i in r.rejected] == [("triage.not_quoted", "error")]


def test_briefs_name_every_reason_code():
    from importlib.resources import files

    for name in ("rules.md", "redraft_new.md"):  # rules.md: the ingest brief's rules (shared with kb eval extract)
        text = " ".join(files("kb.ingest").joinpath("templates", name).read_text(encoding="utf-8").split())
        assert all(code in text for code in NONE_CODES), name
        assert '"none: <code>: <reason>"' in text, name
        assert "at least one of the named records must quote that segment" in text and "each must quote" not in text, name
