"""Agent-driven mode end to end (0.3.0): a scripted "agent" sets up the knowledge base from a docs-only repository.

The agent runs only the commands the playbook names (`kb todo`, the `command` of each `do` item, `kb ingest --report`,
`kb clarify`, `kb redraft`, `kb redraft --report`, `kb check --strict`, `kb baseline freeze`) and writes the files an
agent would: drafted records, triage entries, the person's answers in kb.config.yaml. The person's answers are fixed
strings; kb never supplies them.
"""

import json
import re
import shlex
from pathlib import Path

from helpers import commit, invoke, make_git_repo, write

SPEC = (
    "# Checkout\n\nCustomers pay by card.\n\n"
    "# Performance\n\nThe checkout page must be fast.\n"
)
REQ = "drafted_by: agent:test via kb {brief} brief v1\nid: {id}\nstatement: {statement}\nevidence:\n  quotes:\n{quotes}modules:\n- MOD-checkout\n{flags}"
TARGET = "The checkout page loads in under 2 seconds."


def req(rid: str, statement: str, quotes: list[tuple[str, str]], brief: str = "ingest", flags: str = "") -> str:
    lines = "".join(f"  - source: {src}\n    text: {text}\n" for src, text in quotes)
    return REQ.format(brief=brief, id=rid, statement=statement, quotes=lines, flags=flags)


def kb(repo: Path, *args: str, code: int = 0):
    r = invoke(repo, *args)
    assert r.exit_code == code, r.output
    return r


def todo(repo: Path) -> dict:
    return json.loads(kb(repo, "todo", "--format", "json").stdout)


def do_ids(t: dict) -> list[str]:
    return [d["id"] for d in t["do"]]


def ask_ids(t: dict) -> list[str]:
    return [a["id"] for a in t["ask"]]


def run_do(repo: Path, t: dict) -> None:
    """Run each `do` item's command, as the playbook says."""
    for item in t["do"]:
        assert item["command"], item
        argv = shlex.split(item["command"])
        assert argv[0] == "kb", item
        kb(repo, *argv[1:])


def answer_owner(repo: Path, t: dict, key: str, handle: str) -> None:
    """Follow the owner ask's option: write the handle into kb.config.yaml where it says, then commit it alone."""
    (option,) = next(a for a in t["ask"] if a["id"] == f"owner:{key}")["options"]
    assert option["then"].startswith(f"write it into kb.config.yaml at {key}") and "commit" in option["then"]
    cfg = repo / "kb.config.yaml"
    leaf = key.rsplit(".", 1)[1]
    if key.startswith("modules."):  # modules.<id>.owner
        module = key.split(".")[1]
        text = cfg.read_text()
        start = text.index(f"- id: {module}\n")
        end = text.index("owner: TODO", start)
        cfg.write_text(text[:end] + f'owner: "{handle}"' + text[end + len("owner: TODO"):])
    else:  # policy.approvers.<intent|decisions>
        cfg.write_text(cfg.read_text().replace(f"{leaf}: [TODO]", f'{leaf}: ["{handle}"]', 1))


def set_up_from_docs(tmp_path: Path) -> Path:
    """Steps 1 to 8: from a docs-only repository to a frozen baseline v1, checking kb todo after each step."""
    # 1. a docs-only repository with a vague line
    repo = make_git_repo(None, tmp_path, extra={"docs/spec.md": SPEC})

    # 2. nothing is set up yet
    t = todo(repo)
    assert do_ids(t) == ["init"] and t["ask"] == [] and t["state"]["initialized"] is False

    # 3. kb init, committed: ingest everything; the approvers are the person's to name
    run_do(repo, t)
    commit(repo, "kb init")
    t = todo(repo)
    assert do_ids(t) == ["ingest:all"]
    assert ask_ids(t) == ["owner:policy.approvers.decisions", "owner:policy.approvers.intent"]
    assert t["state"]["clean"] is False and t["state"]["blocking_asks"] == 2

    # 4. kb ingest; the agent follows the brief: one module (no owner in the documents), two requirements, one of
    #    them flagged vague, every segment triaged
    run_do(repo, t)
    brief = (repo / ".kb/cache/ingest/brief.md").read_text()
    assert "### SRC-spec#seg-01" in brief and "### SRC-spec#seg-02" in brief and "The checkout page must be fast." in brief
    kb(repo, "module", "add", "MOD-checkout", "--owner", "TODO", "--title", "Checkout")
    write(repo, "kb/requirements/REQ-F-001.yaml",
          req("REQ-F-001", "Customers pay by card.", [("SRC-spec#seg-01", "Customers pay by card.")]))
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          req("REQ-NF-001", "The checkout page must be fast.", [("SRC-spec#seg-02", "The checkout page must be fast.")],
              flags="flags:\n- vague\n"))
    write(repo, "kb/sources/SRC-spec/triage.yaml", "segments:\n  seg-01:\n  - REQ-F-001\n  seg-02:\n  - REQ-NF-001\n")
    out = kb(repo, "ingest", "--report", "--strict")
    assert "## Accepted (2)\n\n- REQ-F-001\n- REQ-NF-001\n" in out.stdout and "## Rejected (0)" in out.stdout
    commit(repo, "drafts")

    # 5. nothing left to do; four questions: the vague line, the module owner and both approvers
    t = todo(repo)
    assert t["do"] == []
    assert ask_ids(t) == [
        "owner:modules.MOD-checkout.owner", "owner:policy.approvers.decisions", "owner:policy.approvers.intent",
        "vague:REQ-NF-001",
    ]
    vague = next(a for a in t["ask"] if a["id"] == "vague:REQ-NF-001")
    assert vague["evidence"]["quote"] == "The checkout page must be fast." and vague["evidence"]["source"] == "SRC-spec#seg-02"
    assert [o["key"] for o in vague["options"]] == ["target", "keep", "retire"]
    text = kb(repo, "todo").stdout
    assert "REQ-NF-001 says `The checkout page must be fast.` but gives no measurable target." in text

    # 6. the person answers "1a @acme/checkout, 2a @acme/architects, 3a @acme/product, 4a under 2 seconds"
    answer_owner(repo, t, "modules.MOD-checkout.owner", "@acme/checkout")
    answer_owner(repo, t, "policy.approvers.decisions", "@acme/architects")
    answer_owner(repo, t, "policy.approvers.intent", "@acme/product")
    commit(repo, "owners and approvers")
    out = kb(repo, "clarify", "--topic", "Checkout page load target", "--text", TARGET, "--by", "@acme/product",
             "--date", "2026-11-25")
    ref = re.fullmatch(r"recorded in docs/clarifications\.md; quote it as (SRC-clarifications#seg-\d+)\n", out.stdout)
    assert ref, out.stdout
    ref = ref.group(1)
    kb(repo, "redraft", "REQ-NF-001", "--new")
    brief = (repo / ".kb/cache/redraft/brief.md").read_text()
    assert "## REQ-NF-001" in brief and f"### {ref}" in brief and TARGET in brief
    # the agent redrafts REQ-NF-001 with the recorded target, drops `vague`, and triages the clarifications
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          req("REQ-NF-001", TARGET, [("SRC-spec#seg-02", "The checkout page must be fast."), (ref, TARGET)], brief="redraft"))
    seg = ref.split("#")[1]
    write(repo, "kb/sources/SRC-clarifications/triage.yaml",
          "segments:\n" + "".join(
              f"  {s}:\n  - REQ-NF-001\n" if s == seg else f"  {s}: 'none: heading of the clarifications file'\n"
              for s in sorted(re.findall(r"### SRC-clarifications#(seg-\d+)", brief))
          ))
    out = kb(repo, "redraft", "--report", "--strict")
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout and "## Rejected (0)" in out.stdout
    assert "## Still flagged (0)" in out.stdout
    commit(repo, "REQ-NF-001: checkout page target")

    # 7. no errors, nothing to do, no blocking asks: only the optional freeze is asked
    t = todo(repo)
    assert t["do"] == [] and ask_ids(t) == ["freeze:v1"]
    assert t["state"]["clean"] is True and t["state"]["errors"] == 0 and t["state"]["baseline"] is None
    kb(repo, "check", "--strict")

    # 8. the person says freeze: kb baseline freeze v1, committed
    kb(repo, "baseline", "freeze", "v1")
    commit(repo, "baseline v1")
    return repo


def test_agent_driven_setup_from_docs(tmp_path):
    repo = set_up_from_docs(tmp_path)
    t = todo(repo)
    assert t["do"] == [] and t["ask"] == []
    assert t["state"]["clean"] is True and t["state"]["baseline"] == "v1"
    assert (repo / "kb/baseline/v1.yaml").is_file()
    assert "Decided by @acme/product on 2026-11-25." in (repo / "docs/clarifications.md").read_text()
    kb(repo, "check", "--strict")


def test_new_document_after_freeze_is_detected(tmp_path):
    repo = set_up_from_docs(tmp_path)
    write(repo, "docs/refund-policy.md", "# Refunds\n\nRefunds are paid within 14 days.\n")

    t = todo(repo)
    assert do_ids(t) == ["ingest:docs/refund-policy.md"]
    (item,) = t["do"]
    assert item["command"] == "kb ingest docs/refund-policy.md" and item["path"] == "docs/refund-policy.md"
    assert t["state"]["clean"] is False

    out = kb(repo, "check")
    assert "warning source.not_ingested" in out.output and "docs/refund-policy.md" in out.output
