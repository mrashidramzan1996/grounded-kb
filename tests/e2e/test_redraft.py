"""kb redraft end to end on a Markdown source: a vague line gains a target, the record is redrafted and gated."""

from helpers import git, invoke, make_git_repo, write

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-web\n    owner: TODO\n'
DOC = "# Speed\n\nPages should load fast.\n"
REQ = (
    "id: REQ-NF-001\nstatement: {statement}\nevidence:\n  quotes:\n  - source: SRC-product#seg-01\n"
    "    text: {quote}\nmodules: [MOD-web]\n{flags}drafted_by: agent:test via kb ingest brief v1\n"
)


def commit(repo, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def test_redraft_flow_markdown_source(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/product.md": DOC})

    # 1. ingest, the "agent" drafts a vague requirement, the drafts pass and are committed
    assert invoke(repo, "ingest", "docs/").exit_code == 0
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load fast.", quote="Pages should load fast.", flags="flags: [vague]\n"))
    write(repo, "kb/sources/SRC-product/triage.yaml", "segments:\n  seg-01: [REQ-NF-001]\n")
    out = invoke(repo, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output
    commit(repo, "drafts")

    # 2. the document gains a target; sync names the record that cites the changed text
    write(repo, "docs/product.md", DOC.replace("load fast.", "load in under 2 seconds."))
    out = invoke(repo, "source", "sync")
    assert out.exit_code == 0, out.output
    assert "1 records cite changed text: REQ-NF-001 (vague). Run `kb redraft --changed` to redraft them." in out.stderr

    # 3. kb redraft --changed writes the brief for that record only
    out = invoke(repo, "redraft", "--changed")
    assert out.exit_code == 0, out.output
    brief = (repo / ".kb/cache/redraft/brief.md").read_text()
    assert "## REQ-NF-001" in brief and "cites changed text SRC-product#seg-01" in brief
    assert "Pages should load in under 2 seconds." in brief

    # 4. the "agent" follows the brief: new statement and quote, `vague` dropped, the same id and file
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load in under 2 seconds.", quote="Pages should load in under 2 seconds.", flags=""))

    # 5. the report accepts it
    out = invoke(repo, "redraft", "--report", "--strict")
    assert out.exit_code == 0, out.output
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout
    assert "## Rejected (0)" in out.stdout and "## Still flagged (0)" in out.stdout
    assert invoke(repo, "check").exit_code == 0


ADR = (
    "---\nid: {id}\ntitle: Hosting\n{sup}governs: [MOD-web]\nevidence:\n  quotes:\n  - source: SRC-hosting#seg-01\n"
    "    text: {quote}\ndrafted_by: agent:test via kb redraft brief v1\n---\nWe host where the document says.\n"
)


def test_supersede_adr_after_document_change(tmp_path):
    doc = "# Hosting\n\nThe service runs on AWS.\n"
    config = CONFIG.replace("owner: TODO", 'owner: "@lee"')
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/hosting.md": doc})
    assert invoke(repo, "ingest", "docs/").exit_code == 0
    write(repo, "kb/decisions/ADR-0001.md", ADR.format(id="ADR-0001", sup="", quote="The service runs on AWS."))
    write(repo, "kb/sources/SRC-hosting/triage.yaml", "segments:\n  seg-01: [ADR-0001]\n")
    out = invoke(repo, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output
    commit(repo, "decision")
    accepted = (repo / "kb/decisions/ADR-0001.md").read_bytes()

    # the document changes: the accepted decision's quote is gone, and it may not be edited
    write(repo, "docs/hosting.md", doc.replace("AWS", "GCP"))
    out = invoke(repo, "source", "sync")
    assert out.exit_code == 0 and "ADR-0001" in out.stderr
    out = invoke(repo, "check", "--strict")
    assert out.exit_code == 1 and "error quote.not_found" in out.output
    out = invoke(repo, "redraft", "ADR-0001")
    assert out.exit_code == 0, out.output

    # the "agent" supersedes it with the next free id, quoting the new text; the old file is untouched
    write(repo, "kb/decisions/ADR-0002.md", ADR.format(id="ADR-0002", sup="supersedes: ADR-0001\n", quote="The service runs on GCP."))
    write(repo, "kb/sources/SRC-hosting/triage.yaml", "segments:\n  seg-01: [ADR-0002]\n")
    assert (repo / "kb/decisions/ADR-0001.md").read_bytes() == accepted

    out = invoke(repo, "redraft", "--report", "--strict")
    assert out.exit_code == 0, out.output
    assert "- ADR-0002 (supersedes a selected record)" in out.stdout and "## Rejected (0)" in out.stdout
    out = invoke(repo, "check", "--strict")
    assert out.exit_code == 0, out.output


def test_changed_and_new_text_redrafted_and_drafted(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/product.md": DOC})
    assert invoke(repo, "ingest", "docs/").exit_code == 0
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load fast.", quote="Pages should load fast.", flags="flags: [vague]\n"))
    write(repo, "kb/sources/SRC-product/triage.yaml", "segments:\n  seg-01: [REQ-NF-001]\n")
    out = invoke(repo, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output
    commit(repo, "drafts")

    # 1. the document gains a target for the cited sentence and a new section; sync names both
    refunds = "Refunds are paid within 14 days."
    write(repo, "docs/product.md", DOC.replace("load fast.", "load in under 2 seconds.") + f"\n# Refunds\n\n{refunds}\n")
    out = invoke(repo, "source", "sync")
    assert out.exit_code == 0, out.output
    assert out.stderr.splitlines() == [
        "1 records cite changed text: REQ-NF-001 (vague).",
        "1 new segments have no records yet: SRC-product#seg-02.",
        "Run `kb redraft --changed --new` to redraft and draft them.",
    ]

    # 2. one brief: the record to redraft, then the new text to draft from
    out = invoke(repo, "redraft", "--changed", "--new")
    assert out.exit_code == 0, out.output
    brief = (repo / ".kb/cache/redraft/brief.md").read_text()
    assert "## REQ-NF-001" in brief and "## New text to draft from" in brief and refunds in brief
    assert "- REQ-NF-001: `Pages load fast.`" in brief

    # 3. the "agent" redrafts REQ-NF-001 in place, drafts REQ-F-001 from the new section and triages both
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load in under 2 seconds.", quote="Pages should load in under 2 seconds.", flags=""))
    write(repo, "kb/requirements/REQ-F-001.yaml",
          REQ.format(statement=refunds, quote=refunds, flags="").replace("REQ-NF-001", "REQ-F-001")
          .replace("seg-01", "seg-02").replace("kb ingest brief", "kb redraft brief"))
    write(repo, "kb/sources/SRC-product/triage.yaml", "segments:\n  seg-01: [REQ-NF-001]\n  seg-02: [REQ-F-001]\n")

    # 4. the report accepts both, and the KB checks clean
    out = invoke(repo, "redraft", "--report", "--strict")
    assert out.exit_code == 0, out.output
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout and "## Drafted (1)\n\n- REQ-F-001\n" in out.stdout
    assert "## Rejected (0)" in out.stdout and "## Flagged (0)" in out.stdout
    assert invoke(repo, "check").exit_code == 0
