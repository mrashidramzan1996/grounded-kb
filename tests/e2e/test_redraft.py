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
