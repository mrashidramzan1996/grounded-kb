"""`kb graph`: records, modules and their links as JSON or Mermaid; entity_key and entity_candidates (0.4.0, Task 6)."""

import json
import re

from helpers import git, invoke, make_git_repo, ok, stored_repo, write

from kb.config.schema import KbConfig
from kb.graph import build_graph, entity_candidates, entity_key, render_mermaid
from kb.records.models import Term

DOC = "# Billing\n\nAn account is a customer of Acme. Each account owns its invoices.\n\n# Payment\n\nAccounts are billed monthly.\n"
CONFIG = KbConfig.model_validate({"version": 1, "modules": [{"id": "MOD-a", "owner": "@alice", "title": "Accounts"}]})

ACCOUNT = """\
id: TERM-account
name: account
definition: A customer of Acme.
kind: actor
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: An account is a customer of Acme.
relations:
- type: owns
  target: TERM-invoice
  quote:
    source: SRC-spec#seg-01
    text: Each account owns its invoices.
"""
INVOICE = """\
id: TERM-invoice
name: invoice
aliases: [invoices]
definition: A bill sent to an account.
kind: data
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: Each account owns its invoices.
"""
REQ = """\
id: REQ-F-001
statement: An account owns its invoices.
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: Each account owns its invoices.
features: [FEAT-001]
modules: [MOD-a]
actor: TERM-account
entities: [TERM-invoice]
"""
FEAT = "---\nid: FEAT-001\ntitle: Invoices\nmodule: MOD-a\nclaims:\n- id: FEAT-001.C1\n  claim: Lists invoices.\n  requirement: REQ-F-001\n---\n"
ADR_1 = "---\nid: ADR-0001\ntitle: Bill monthly\ngoverns: [MOD-a]\n---\nWhy.\n"
ADR_2 = "---\nid: ADR-0002\ntitle: Bill weekly\nsupersedes: ADR-0001\ngoverns: [REQ-F-001]\n---\nWhy.\n"
EXCL = ("id: EXCL-001\nstatement: Monthly billing is out of scope.\nevidence:\n  quotes:\n  - source: SRC-spec#seg-02\n"
        "    text: Accounts are billed monthly.\nmodules: [MOD-a]\n")
FILES = {
    "kb/glossary/TERM-account.yaml": ACCOUNT,
    "kb/glossary/TERM-invoice.yaml": INVOICE,
    "kb/requirements/REQ-F-001.yaml": REQ,
    "kb/features/FEAT-001.md": FEAT,
    "kb/decisions/ADR-0001.md": ADR_1,
    "kb/decisions/ADR-0002.md": ADR_2,
    "kb/exclusions/EXCL-001.yaml": EXCL,
}


def edge(source, target, type, quote=None):
    return {"source": source, "target": target, "type": type, "quote": quote}


def test_graph_nodes_and_edges():
    repo = stored_repo(DOC, FILES)
    assert repo.errors == []
    g = build_graph(repo, CONFIG)
    assert [(n["id"], n["type"]) for n in g["nodes"]] == [
        ("ADR-0001", "decision"), ("ADR-0002", "decision"), ("EXCL-001", "exclusion"), ("FEAT-001", "feature"),
        ("FEAT-001.C1", "claim"), ("MOD-a", "module"), ("REQ-F-001", "requirement"), ("TERM-account", "term"),
        ("TERM-invoice", "term"),
    ]
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    assert labels["TERM-account"] == "account" and labels["MOD-a"] == "Accounts" and labels["ADR-0002"] == "Bill weekly"
    assert g["edges"] == [
        edge("ADR-0002", "ADR-0001", "supersedes"),
        edge("ADR-0002", "REQ-F-001", "governs"),
        edge("EXCL-001", "MOD-a", "excludes"),
        edge("FEAT-001.C1", "REQ-F-001", "implements"),
        edge("REQ-F-001", "FEAT-001", "belongs_to"),
        edge("REQ-F-001", "MOD-a", "belongs_to"),
        edge("REQ-F-001", "TERM-account", "actor"),
        edge("REQ-F-001", "TERM-invoice", "mentions"),
        edge("TERM-account", "TERM-invoice", "owns", "Each account owns its invoices."),
    ]
    # deterministic whatever the load order
    assert build_graph(stored_repo(DOC, dict(reversed(list(FILES.items())))), CONFIG) == g
    # a rejected record and its edges are gone; an edge to a missing record is dropped
    rejected = stored_repo(DOC, FILES | {"kb/glossary/TERM-invoice.yaml": INVOICE + "status: rejected\n"})
    g2 = build_graph(rejected, CONFIG)
    assert "TERM-invoice" not in {n["id"] for n in g2["nodes"]}
    assert all("TERM-invoice" not in (e["source"], e["target"]) for e in g2["edges"])


def test_mermaid_escapes_labels():
    evil = 'a"]--> x'
    hostile = ACCOUNT.replace("name: account", f"name: '{evil}'\naliases: [account]")
    long = REQ.replace("statement: An account owns its invoices.", "statement: " + "An account owns its invoices. " * 5)
    repo = stored_repo(DOC, FILES | {"kb/glossary/TERM-account.yaml": hostile, "kb/requirements/REQ-F-001.yaml": long})
    assert repo.errors == []
    out = render_mermaid(build_graph(repo, CONFIG))
    lines = out.splitlines()
    assert lines[0] == "flowchart LR"
    node = re.compile(r'    (n\d+)\["([^"\[\]|<>`\n]*)"\]')
    link = re.compile(r"    (n\d+) -->\|([a-z_]+)\| (n\d+)")
    nodes = [m for l in lines[1:] if (m := node.fullmatch(l))]
    links = [m for l in lines[1:] if (m := link.fullmatch(l))]
    assert len(nodes) + len(links) == len(lines) - 1 == 9 + 9
    assert [m.group(1) for m in nodes] == [f"n{i}" for i in range(1, 10)]
    assert all(len(m.group(2)) <= 60 for m in nodes)
    (term,) = [m.group(2) for m in nodes if m.group(2).startswith("TERM-account")]
    assert term == "TERM-account: a-- x"
    assert evil not in out and "TERM-account" not in "".join(m.group(0) for m in links)
    assert {m.group(2) for m in links} >= {"owns", "belongs_to", "actor", "governs"}


def test_graph_cli(tmp_path):
    config = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/spec.md": DOC})
    ok(root, "ingest")
    for rel, text in FILES.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-m", "records")
    data = json.loads(ok(root, "graph").stdout)
    assert data == json.loads(ok(root, "graph", "--format", "json").stdout)
    assert {"source": "TERM-account", "target": "TERM-invoice", "type": "owns", "quote": "Each account owns its invoices."} in data["edges"]
    assert ok(root, "graph", "--format", "mermaid").stdout.startswith("flowchart LR\n")
    bad = invoke(root, "graph", "--format", "dot")
    assert bad.exit_code == 2 and "unknown format" in bad.output


def test_entity_key():
    t = Term(id="TERM-billing-admin", name="Billing  Admin", aliases=["billing\nadmins", "BA"], definition="d")
    assert entity_key(t) == frozenset({"billing admin", "billing admins", "ba"})


CANDIDATE_DOC = """\
# Acme Ledger Spec

"Billing Admin" means the person who pays. The Seat Pool holds unused seats.

## Monthly Reports

Each account draws from the Seat Pool. Monthly Reports are sent on the first day.

Monthly Reports are archived. Acme Ledger is the system of record. See REQ-F-001 Seat Pool.

The Acme Ledger Spec is referenced by Acme Ledger Spec readers.
"""


def test_entity_candidates():
    repo = stored_repo(CANDIDATE_DOC, {})
    got = entity_candidates(repo)
    assert [(phrase, ref) for phrase, ref, _ in got] == [
        ("acme ledger", "SRC-spec#seg-02"), ("billing admin", "SRC-spec#seg-01"), ("seat pool", "SRC-spec#seg-01"),
    ]
    assert [s.text for *_, s in got] == ["Acme Ledger is the system of record.", '"Billing Admin" means the person who pays.',
                                         "The Seat Pool holds unused seats."]
    # once a term names it (here through an alias) it is no longer a candidate
    admin = "id: TERM-billing-admin\nname: admin\naliases: [Billing Admin]\ndefinition: The person who pays.\n"
    repo = stored_repo(CANDIDATE_DOC, {"kb/glossary/TERM-billing-admin.yaml": admin})
    assert [phrase for phrase, *_ in entity_candidates(repo)] == ["acme ledger", "seat pool"]
    # a rejected term covers nothing
    repo = stored_repo(CANDIDATE_DOC, {"kb/glossary/TERM-billing-admin.yaml": admin + "status: rejected\n"})
    assert [phrase for phrase, *_ in entity_candidates(repo)] == ["acme ledger", "billing admin", "seat pool"]
