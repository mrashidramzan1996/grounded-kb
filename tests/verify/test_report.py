import json

from kb.issues import Issue
from kb.ports.forge import COMMENT_MARKER
from kb.verify.engine import Verdict
from kb.verify.report import (
    COMMENT_LIMIT,
    VerifyReport,
    render_json,
    render_markdown,
    summary_line,
)


def verdict(id, status="verified", kind="requirement", reasons=()):
    return Verdict(id, kind, status, "E1", tuple(reasons), (), ())


def report(verdicts=(), issues=(), blocking=(), max_items=40):
    return VerifyReport(
        base_ref=None, head="h" * 40, verdicts=list(verdicts), issues=list(issues), blocking=list(blocking),
        mode="warn-only", scope="all", max_items=max_items,
    )


def section(md: str, title: str) -> str:
    return md.split(f"### {title}\n", 1)[1].split("\n### ", 1)[0]


def test_built_and_not_built_are_counted():
    r = report([verdict("REQ-F-001", "built"), verdict("REQ-F-002", "not_built"), verdict("REQ-F-003", "not_built")])
    summary = json.loads(render_json(r))["summary"]
    assert summary == {"broken": 0, "stale": 0, "unverified": 0, "verified": 0, "built": 1, "not_built": 2,
                       "errors": 0, "warnings": 0}
    assert summary_line(r) == (
        "0 broken, 0 stale, 0 unverified, 0 verified, 1 built, 2 not_built; 0 blocking (mode: warn-only)"
    )
    assert render_markdown(r).splitlines()[1] == (
        "**kb verify**: 0 broken · 0 stale · 0 unverified · 0 verified · 1 built · 2 not_built (mode: warn-only)"
    )


def test_blocking_and_other_issue_lists_are_capped():
    blocking = [Issue("schema.x", f"b{n:02}", record=f"R-{n:02}") for n in range(7)]
    other = [Issue("x.warn", f"o{n:02}", "warning", record=f"W-{n:02}") for n in range(9)]
    md = render_markdown(report(issues=blocking + other, blocking=blocking, max_items=3))
    block, rest = section(md, "Blocking"), section(md, "Other issues")
    assert [ln for ln in block.splitlines() if ln.startswith("- ")] == [
        "- error **schema.x** `R-00`: b00", "- error **schema.x** `R-01`: b01", "- error **schema.x** `R-02`: b02"]
    assert "… and 4 more" in block
    assert len([ln for ln in rest.splitlines() if ln.startswith("- ")]) == 3 and "… and 6 more" in rest


def test_comment_body_stays_under_the_size_limit():
    big = [Issue("schema.x", "y" * 5000, record=f"R-{n:02}") for n in range(40)]
    md = render_markdown(report([verdict(f"REQ-F-{n:03}", "broken", reasons=["z" * 3000]) for n in range(40)],
                                issues=big, blocking=big))
    assert len(md) <= COMMENT_LIMIT < 65536  # GitHub's comment limit
    assert md.startswith(COMMENT_MARKER + "\n")
    assert md.endswith("\n… comment truncated; see the status.json artifact\n")
    assert render_markdown(report()).count("truncated") == 0


def test_cell_text_cannot_inject_links_or_images():
    evil = "see ![x](https://evil.example/p.png) and [click](https://evil.example)"
    issue = Issue("schema.x", evil, record="R-1")
    md = render_markdown(report([verdict("REQ-F-001", "broken", reasons=[evil])], issues=[issue], blocking=[issue]))
    assert "](" not in md.replace("\\](", "") and "![" not in md and "[click]" not in md
    assert "\\!\\[x\\](https://evil.example/p.png)" in md
