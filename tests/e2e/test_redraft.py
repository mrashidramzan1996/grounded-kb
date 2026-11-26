"""kb redraft end to end on a Markdown source: a vague line gains a target, the record is redrafted and gated."""

from helpers import git, invoke, make_git_repo, write

CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-web\n    owner: TODO\n'
DOC = "# Speed\n\nPages should load fast.\n"
REQ = (
    "id: REQ-NF-001\nstatement: {statement}\nevidence:\n  quotes:\n  - source: SRC-product#seg-01\n"
    "    text: {quote}\nmodules: [MOD-web]\n"
    "modality: should\nactor: the pages\ncategory: performance\norigin: stated\n{flags}drafted_by: agent:test via kb ingest brief v1\n"
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
    assert out.stderr.splitlines() == [
        "1 record cites changed text: REQ-NF-001 (vague).",
        "Before committing, run `kb redraft --changed` to redraft them.",
    ]

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


def _req(rid: str, text: str, seg: str) -> str:
    req = REQ.format(statement=text, quote=text, flags="")
    req = req if rid.startswith("REQ-NF-") else req.replace("category: performance\n", "")  # category is for REQ-NF only
    req = req.replace("actor: the pages", "actor: " + text.split()[0].lower())  # the actor is named in the quote
    return (req.replace("REQ-NF-001", rid)
            .replace("seg-01", seg).replace("kb ingest brief", "kb redraft brief"))


def test_changed_and_new_text_redrafted_and_drafted(tmp_path):
    doc = DOC + "\n# Support\n\nSupport is friendly.\n"
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/product.md": doc})
    assert invoke(repo, "ingest", "docs/").exit_code == 0
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load fast.", quote="Pages should load fast.", flags="flags: [vague]\n"))
    write(repo, "kb/sources/SRC-product/triage.yaml",
          "segments:\n  seg-01: [REQ-NF-001]\n  seg-02: 'none: other: no requirement'\n")
    out = invoke(repo, "ingest", "--report", "--strict")
    assert out.exit_code == 0, out.output
    commit(repo, "drafts")

    # 1. the cited sentence gains a target, the uncited "Support" text now states one, and a section is added;
    #    sync names the record and both segments with no records
    support, refunds = "Support answers within 24 hours.", "Refunds are paid within 14 days."
    write(repo, "docs/product.md", doc.replace("load fast.", "load in under 2 seconds.")
          .replace("Support is friendly.", support) + f"\n# Refunds\n\n{refunds}\n")
    out = invoke(repo, "source", "sync")
    assert out.exit_code == 0, out.output
    assert out.stderr.splitlines() == [
        "1 record cites changed text: REQ-NF-001 (vague).",
        "2 new or changed segments have no records yet: SRC-product#seg-02, SRC-product#seg-03.",
        "Before committing, run `kb redraft --changed --new` to redraft and draft them.",
    ]

    # 2. one brief: the record to redraft, then the new and changed text to draft from
    out = invoke(repo, "redraft", "--changed", "--new")
    assert out.exit_code == 0, out.output
    brief = (repo / ".kb/cache/redraft/brief.md").read_text()
    part2 = brief.split("## New text to draft from", 1)[1]
    assert "## REQ-NF-001" in brief and support in part2 and refunds in part2
    assert "- REQ-NF-001: `Pages load fast.`" in brief

    # 3. the "agent" redrafts REQ-NF-001 in place, drafts one requirement from each segment and triages all three
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load in under 2 seconds.", quote="Pages should load in under 2 seconds.", flags=""))
    write(repo, "kb/requirements/REQ-NF-002.yaml", _req("REQ-NF-002", support, "seg-02"))
    write(repo, "kb/requirements/REQ-F-001.yaml", _req("REQ-F-001", refunds, "seg-03"))
    write(repo, "kb/sources/SRC-product/triage.yaml",
          "segments:\n  seg-01: [REQ-NF-001]\n  seg-02: [REQ-NF-002]\n  seg-03: [REQ-F-001]\n")

    # 4. the report accepts all of it, and the KB checks clean
    out = invoke(repo, "redraft", "--report", "--strict")
    assert out.exit_code == 0, out.output
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout
    assert "## Drafted (2)\n\n- REQ-F-001\n- REQ-NF-002\n" in out.stdout
    assert "## Rejected (0)" in out.stdout and "## Flagged (0)" in out.stdout
    assert invoke(repo, "check").exit_code == 0


def test_committed_sync_with_base_flow(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/product.md": DOC})
    assert invoke(repo, "ingest", "docs/").exit_code == 0
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load fast.", quote="Pages should load fast.", flags="flags: [vague]\n"))
    write(repo, "kb/sources/SRC-product/triage.yaml", "segments:\n  seg-01: [REQ-NF-001]\n")
    assert invoke(repo, "ingest", "--report", "--strict").exit_code == 0
    commit(repo, "drafts")
    pre = git(repo, "rev-parse", "HEAD").strip()

    # the document change and the sync are committed before redrafting
    write(repo, "docs/product.md", DOC.replace("load fast.", "load in under 2 seconds."))
    assert invoke(repo, "source", "sync").exit_code == 0
    commit(repo, "doc + sync")

    # --base names the commit before the sync: the brief and the report compare the same range
    out = invoke(repo, "redraft", "--changed", "--base", pre)
    assert out.exit_code == 0, out.output
    assert "cites changed text SRC-product#seg-01" in (repo / ".kb/cache/redraft/brief.md").read_text()
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          REQ.format(statement="Pages load in under 2 seconds.", quote="Pages should load in under 2 seconds.", flags=""))
    out = invoke(repo, "redraft", "--report", "--strict")
    assert out.exit_code == 0, out.output
    assert f"Base: {pre}" in out.stdout and "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout
