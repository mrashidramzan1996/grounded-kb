"""Shared fixture of the judge and findings tests: a document, kb records and a run B over it."""

from pathlib import Path

from helpers import git, invoke, make_git_repo

from kb.eval.extract import RUN_B

SPEC = (
    "# Billing\n\nA seat costs 5 EUR per month. Refunds are never given for a removed seat. "
    "Invoices must be paid within 30 days. Gift cards are not supported in this release.\n\n"
    "# Accounts\n\nAn account is a customer of Acme. Each account owns its invoices. "
    'Each account buys seats for its users. A "Billing Admin" means a user who manages seats.\n'
)


def quoted(seg: str, text: str, indent: str = "  ") -> str:
    return f"{indent}quotes:\n{indent}- source: SRC-spec#{seg}\n{indent}  text: '{text}'\n"


FILES = {
    "kb/requirements/REQ-F-001.yaml": (
        "id: REQ-F-001\nstatement: A seat costs 5 EUR per month.\nevidence:\n" + quoted("seg-01", "A seat costs 5 EUR per month.")
        + "modules: [MOD-a]\nmodality: must\nactor: the billing system\n"
    ),
    "kb/requirements/REQ-F-002.yaml": (
        "id: REQ-F-002\nstatus: rejected\nstatement: Refunds are never given.\nevidence:\n"
        + quoted("seg-01", "Refunds are never given for a removed seat.") + "modules: [MOD-a]\n"
    ),
    "kb/exclusions/EXCL-001.yaml": (
        "id: EXCL-001\nstatement: Gift cards are out of scope.\nevidence:\n"
        + quoted("seg-01", "Gift cards are not supported in this release.") + "modules: [MOD-a]\n"
    ),
    "kb/decisions/ADR-0001.md": "---\nid: ADR-0001\ntitle: No quote\ngoverns: [MOD-a]\n---\nWhy.\n",
    "kb/glossary/TERM-account.yaml": (
        "id: TERM-account\nname: account\naliases: [accounts]\ndefinition: A customer of Acme.\nkind: actor\nevidence:\n"
        + quoted("seg-02", "An account is a customer of Acme.")
        + "relations:\n- type: owns\n  target: TERM-invoice\n  quote:\n    source: SRC-spec#seg-02\n"
        "    text: Each account owns its invoices.\n"
    ),
    "kb/glossary/TERM-invoice.yaml": (
        "id: TERM-invoice\nname: invoice\naliases: [invoices]\ndefinition: A bill.\nkind: document\nevidence:\n"
        + quoted("seg-02", "Each account owns its invoices.")
    ),
    "kb/glossary/TERM-plan.yaml": "id: TERM-plan\nname: plan\ndefinition: A price list.\n",  # no evidence: not judged
}

RUN_B_FILES = {
    "requirements/B-REQ-F-001.yaml": (
        "id: B-REQ-F-001\nstatement: Seats cost 5 EUR a month.\nevidence:\n" + quoted("seg-01", "A seat costs 5 EUR per month.")
        + "modules: [MOD-a]\n"
    ),
    "requirements/B-REQ-F-002.yaml": (
        "id: B-REQ-F-002\nstatement: Invoices are due within 30 days.\nevidence:\n"
        + quoted("seg-01", "Invoices must be paid within 30 days.") + "modules: [MOD-a]\n"
    ),
    "glossary/B-TERM-account.yaml": (
        "id: B-TERM-account\nname: account\ndefinition: A customer.\nevidence:\n" + quoted("seg-02", "An account is a customer of Acme.")
        + "relations:\n- type: uses\n  target: B-TERM-seat\n  quote:\n    source: SRC-spec#seg-02\n"
        "    text: Each account buys seats for its users.\n"
    ),
    "glossary/B-TERM-seat.yaml": (
        "id: B-TERM-seat\nname: seat\naliases: [seats]\ndefinition: A paid place.\nevidence:\n"
        + quoted("seg-02", "Each account buys seats for its users.")
    ),
    "glossary/B-TERM-billing-admin.yaml": (
        "id: B-TERM-billing-admin\nname: Billing Admin\ndefinition: Manages seats.\nevidence:\n"
        + quoted("seg-02", 'A "Billing Admin" means a user who manages seats.')
    ),
}


def write_run_b(root: Path, files: dict[str, str] = RUN_B_FILES) -> None:
    for rel, text in files.items():
        path = root / RUN_B / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def valid(items: list[dict]) -> dict[str, dict]:
    """A valid verdict for every item, by canonical id."""
    out = {}
    for i in items:
        span = i["text"]
        v: dict = {"id": i["public"], "span": span}  # verdicts answer by public id
        if i["kind"] == "faithful":
            v |= {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": "yes"}
        elif i["kind"] == "entity_faithful":
            v |= {"definition_matches_quote": "yes", "kind_correct": "yes"}
        elif i["kind"] == "relation_faithful":
            v |= {"quote_states_relation": "yes", "direction_correct": "yes"}
        elif i["kind"] in ("uncovered", "only_b"):
            v |= {"verdict": "missing", "kind_hint": "requirement", "severity": "high", "related": ["REQ-F-001"]}
        elif i["kind"] == "split_merge":
            v |= {"verdict": "ok"}
        else:
            v |= {"verdict": "missing", "related": ["TERM-account"]}
        out[i["id"]] = v
    return out


def ingested_with_run_b(tmp_path: Path) -> Path:
    config = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - {id: MOD-a, owner: "@alice"}\n'
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": config, "docs/spec.md": SPEC})
    assert invoke(root, "ingest").exit_code == 0
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-m", "records")
    assert invoke(root, "eval", "extract").exit_code == 0
    write_run_b(root)
    return root
