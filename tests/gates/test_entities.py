"""The entity gate: a term's name in its quotes, relation quotes found and naming both ends, and entity references."""

from helpers import git, invoke, make_git_repo, ok, stored_repo, write

from kb.config.schema import KbConfig
from kb.gates.base import GateContext
from kb.gates.coverage import quote_spans
from kb.gates.entities import entity_gate
from kb.gates.ids import id_gate
from kb.gates.triage import triage_gate
from kb.ingest.report import ingest_report

DOC = "# Billing\n\nAn account is a customer of Acme. Each account owns its invoices.\n\n# Payment\n\nAccounts are billed monthly.\n"
SRC = "SRC-spec"


def term(tid: str, name: str, *, aliases=(), kind="data", quote: str | None = None, seg="seg-01", relations=()) -> str:
    out = f"id: {tid}\nname: {name}\n"
    out += "aliases:\n" + "".join(f"- {a}\n" for a in aliases) if aliases else ""
    out += f"definition: The {name}.\n"
    out += f"kind: {kind}\n" if kind else ""
    if quote is not None:
        out += f"evidence:\n  quotes:\n  - source: {SRC}#{seg}\n    text: {quote}\n"
    if relations:
        out += "relations:\n" + "".join(
            f"- type: {t}\n  target: {target}\n  quote:\n    source: {SRC}#{s}\n    text: {text}\n" for t, target, s, text in relations
        )
    return out


def account(*relations) -> str:
    return term("TERM-account", "account", kind="actor", quote="An account is a customer of Acme.", relations=relations)


INVOICE = term("TERM-invoice", "invoice", aliases=["invoices"], quote="Each account owns its invoices.")


def repo(files: dict[str, str]):
    r = stored_repo(DOC, files)
    assert r.errors == [], r.errors
    return r


MOD_A = KbConfig.model_validate({"version": 1, "modules": [{"id": "MOD-a", "owner": "@alice"}]})


def run(g, files: dict[str, str], config: KbConfig = MOD_A):
    return g(GateContext(None, config, repo(files)))  # type: ignore[arg-type]


def found(issues, code: str):
    return [i for i in issues if i.code == code]


def test_entity_name_must_be_in_quote():
    good = {"kb/glossary/TERM-account.yaml": account(), "kb/glossary/TERM-invoice.yaml": INVOICE}
    assert run(entity_gate, good) == []
    # neither "billing" nor an alias appears in the quote; "accounts" is not "account" (no stemming)
    bad = {"kb/glossary/TERM-billing.yaml": term("TERM-billing", "billing", quote="An account is a customer of Acme."),
           "kb/glossary/TERM-acct.yaml": term("TERM-acct", "account", seg="seg-02", quote="Accounts are billed monthly.")}
    issues = found(run(entity_gate, bad), "entity.name_not_in_quote")
    assert [(i.record, i.severity) for i in issues] == [("TERM-acct", "warning"), ("TERM-billing", "warning")]
    assert issues[1].message == "TERM-billing: neither the name nor an alias appears in its found quotes; quote a passage that names it"
    assert issues[1].path == "kb/glossary/TERM-billing.yaml"
    # case-insensitive, on word boundaries, through an alias
    alias = {"kb/glossary/TERM-acct.yaml": term("TERM-acct", "account", aliases=["ACCOUNTS"], seg="seg-02",
                                                 quote="Accounts are billed monthly.")}
    assert run(entity_gate, alias) == []
    # an unfound evidence quote is quote.not_found; a kind without evidence is entity.unquoted
    gone = {"kb/glossary/TERM-invoice.yaml": term("TERM-invoice", "invoice", quote="Every invoice is paid by card.")}
    # (an unfound quote names nothing, so the name check fails too)
    assert sorted((i.code, i.severity) for i in run(entity_gate, gone)) == [("entity.name_not_in_quote", "warning"),
                                                                            ("quote.not_found", "error")]
    unquoted = run(entity_gate, {"kb/glossary/TERM-invoice.yaml": term("TERM-invoice", "invoice")})
    assert [(i.code, i.severity) for i in unquoted] == [("entity.unquoted", "warning")]
    # a 0.3 term (no kind, no evidence) is fine
    assert run(entity_gate, {"kb/glossary/TERM-invoice.yaml": term("TERM-invoice", "invoice", kind=None)}) == []


def test_relation_quote_must_mention_both_entities():
    owns = ("owns", "TERM-invoice", "seg-01", "Each account owns its invoices.")
    assert run(entity_gate, {"kb/glossary/TERM-account.yaml": account(owns), "kb/glossary/TERM-invoice.yaml": INVOICE}) == []
    billed = ("owns", "TERM-invoice", "seg-02", "Accounts are billed monthly.")
    issues = run(entity_gate, {"kb/glossary/TERM-account.yaml": account(billed), "kb/glossary/TERM-invoice.yaml": INVOICE})
    assert [(i.code, i.severity, i.record) for i in issues] == [("relation.entities_not_in_quote", "warning", "TERM-account")]
    assert issues[0].message == ("TERM-account: relation owns TERM-invoice: the quote does not name both TERM-account and "
                                 "TERM-invoice: `Accounts are billed monthly.`")
    # a relation quote that is not in its segment, or too short, is not found (an error)
    for text in ("Each account owns every invoice.", "owns"):
        bad = ("owns", "TERM-invoice", "seg-01", text)
        issues = run(entity_gate, {"kb/glossary/TERM-account.yaml": account(bad), "kb/glossary/TERM-invoice.yaml": INVOICE})
        assert ("relation.quote_not_found", "error") in [(i.code, i.severity) for i in issues], text


def test_relation_target_must_exist():
    owns = ("owns", "TERM-invoice", "seg-01", "Each account owns its invoices.")
    issues = found(run(id_gate, {"kb/glossary/TERM-account.yaml": account(owns)}), "ref.missing")
    assert [(i.record, i.message) for i in issues] == [("TERM-account", "TERM-account: TERM-invoice does not exist")]
    # the quote checks still run; the missing end is ref.missing only
    assert run(entity_gate, {"kb/glossary/TERM-account.yaml": account(owns)}) == []
    # relation and evidence quotes must cite an existing segment
    nowhere = ("owns", "TERM-invoice", "seg-09", "Each account owns its invoices.")
    files = {"kb/glossary/TERM-account.yaml": account(nowhere), "kb/glossary/TERM-invoice.yaml": INVOICE}
    assert [i.message for i in found(run(id_gate, files), "ref.missing")] == [f"TERM-account: quote source {SRC}#seg-09: no such segment"]


REQ = """\
id: REQ-F-001
statement: An account owns its invoices.
evidence:
  quotes:
  - source: SRC-spec#seg-01
    text: Each account owns its invoices.
modules: [MOD-a]
modality: must
"""


def test_requirement_actor_term_must_exist():
    files = {"kb/glossary/TERM-account.yaml": account(), "kb/glossary/TERM-invoice.yaml": INVOICE}
    linked = REQ + "actor: TERM-account\nentities: [TERM-account, TERM-invoice]\n"
    assert found(run(id_gate, files | {"kb/requirements/REQ-F-001.yaml": linked}), "ref.missing") == []
    missing = REQ + "actor: TERM-payer\nentities: [TERM-invoice, TERM-ledger]\n"
    issues = found(run(id_gate, files | {"kb/requirements/REQ-F-001.yaml": missing}), "ref.missing")
    assert [i.message for i in issues] == ["REQ-F-001: TERM-payer does not exist", "REQ-F-001: TERM-ledger does not exist"]
    # plain-text actors stay allowed
    plain = REQ + "actor: the billing system\n"
    assert found(run(id_gate, files | {"kb/requirements/REQ-F-001.yaml": plain}), "ref.missing") == []


def test_term_quotes_satisfy_triage_but_never_cover():
    tri = {f"kb/sources/{SRC}/triage.yaml": "segments:\n  seg-01: [TERM-invoice]\n  seg-02: [TERM-account]\n"}
    issues = found(run(triage_gate, tri | {"kb/glossary/TERM-account.yaml": account(), "kb/glossary/TERM-invoice.yaml": INVOICE}),
                   "triage.not_quoted")
    # TERM-invoice quotes seg-01; TERM-account quotes seg-01, not seg-02
    assert [i.message for i in issues] == [f"{SRC}#seg-02: triage names `TERM-account` but none of them quotes this segment"]
    # a term without evidence is still skipped
    bare = {"kb/glossary/TERM-account.yaml": term("TERM-account", "account", kind=None)}
    assert found(run(triage_gate, {f"kb/sources/{SRC}/triage.yaml": "segments:\n  seg-02: [TERM-account]\n"} | bare),
                 "triage.not_quoted") == []
    assert quote_spans(repo({"kb/glossary/TERM-account.yaml": account(), "kb/glossary/TERM-invoice.yaml": INVOICE})) == {}


CONFIG = 'version: 1\nsources:\n  - paths: ["docs/**"]\nmodules:\n  - id: MOD-a\n    owner: "@alice"\n'


def test_drafted_relation_errors_are_rejected_in_report(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/spec.md": DOC})
    ok(root, "ingest")
    git(root, "add", "-A")
    git(root, "commit", "-m", "ingest")
    by = "drafted_by: agent:test via kb ingest brief v1\n"
    write(root, "kb/glossary/TERM-invoice.yaml", INVOICE + by)
    write(root, "kb/glossary/TERM-account.yaml", account(("owns", "TERM-invoice", "seg-02", "Accounts are billed monthly.")) + by)
    write(root, "kb/glossary/TERM-billing.yaml", term("TERM-billing", "billing", quote="An account is a customer of Acme.") + by)
    r = ingest_report(root, "HEAD")
    assert sorted((i.record, i.code, i.severity) for i in r.rejected) == [
        ("TERM-account", "relation.entities_not_in_quote", "error"),
        ("TERM-billing", "entity.name_not_in_quote", "error"),
    ]
    assert r.accepted == ["TERM-invoice"]
    assert invoke(root, "ingest", "--report", "--strict").exit_code == 1
    # committed, `kb check` only warns
    git(root, "add", "-A")
    git(root, "commit", "-m", "terms")
    out = invoke(root, "check")
    assert out.exit_code == 0 and "relation.entities_not_in_quote" in out.output and "entity.name_not_in_quote" in out.output
    assert ingest_report(root, "HEAD").rejected == []


# --- fix round 1: aliases that cannot name anything, overlapping ends, found quotes only -----------

def codes_of(issues):
    return sorted((i.code, i.severity, i.record) for i in issues)


def test_punctuation_alias_is_ignored():
    # an alias with fewer than 2 alphanumeric characters would match any quote
    files = {"kb/glossary/TERM-account.yaml": account(("owns", "TERM-ledger", "seg-02", "Accounts are billed monthly.")),
             "kb/glossary/TERM-ledger.yaml": term("TERM-ledger", "ledger", aliases=["'.'"], seg="seg-02", quote="Accounts are billed monthly.")}
    issues = run(entity_gate, files)
    assert codes_of(issues) == [
        ("entity.alias_invalid", "warning", "TERM-ledger"),
        ("entity.name_not_in_quote", "warning", "TERM-ledger"),
        ("relation.entities_not_in_quote", "warning", "TERM-account"),
    ]
    (bad,) = found(issues, "entity.alias_invalid")
    assert bad.message == "TERM-ledger: `.` is ignored as a name or alias: it needs 2 letters or digits and must not be a stop word"


def test_stop_word_alias_is_ignored():
    files = {"kb/glossary/TERM-ledger.yaml": term("TERM-ledger", "ledger", aliases=["a", "The"], quote="An account is a customer of Acme.")}
    assert codes_of(run(entity_gate, files)) == [
        ("entity.alias_invalid", "warning", "TERM-ledger"),
        ("entity.alias_invalid", "warning", "TERM-ledger"),
        ("entity.name_not_in_quote", "warning", "TERM-ledger"),
    ]


def test_relation_ends_must_not_overlap():
    # "account" is found only inside "customer account": one occurrence cannot name both ends
    doc_quote = "An account is a customer of Acme."
    files = {"kb/glossary/TERM-account.yaml": account(("owns", "TERM-customer", "seg-01", doc_quote)),
             "kb/glossary/TERM-customer.yaml": term("TERM-customer", "account is a customer", quote=doc_quote)}
    assert codes_of(run(entity_gate, files)) == [("relation.entities_not_in_quote", "warning", "TERM-account")]
    # with both named apart it passes
    files["kb/glossary/TERM-customer.yaml"] = term("TERM-customer", "customer", quote=doc_quote)
    assert run(entity_gate, files) == []


def test_alias_conflict_with_another_term():
    # the target declares the source's name as its alias: a conflict, and no longer a double mention
    quote = "An account is a customer of Acme."
    files = {"kb/glossary/TERM-account.yaml": account(("owns", "TERM-ledger", "seg-01", quote)),
             "kb/glossary/TERM-ledger.yaml": term("TERM-ledger", "ledger", aliases=["Account"], quote=quote)}
    issues = run(entity_gate, files)
    assert codes_of(issues) == [
        ("entity.alias_conflict", "warning", "TERM-account"),
        ("entity.alias_conflict", "warning", "TERM-ledger"),
        ("relation.entities_not_in_quote", "warning", "TERM-account"),
    ]
    assert [i.message for i in found(issues, "entity.alias_conflict")] == [
        "TERM-account: `account` is also a name or alias of TERM-ledger",
        "TERM-ledger: `account` is also a name or alias of TERM-account",
    ]
    # a rejected term conflicts with nothing
    files["kb/glossary/TERM-ledger.yaml"] += "status: rejected\n"
    assert found(run(entity_gate, files), "entity.alias_conflict") == []


def test_name_must_be_in_a_found_quote():
    any_mode = ("id: TERM-ledger\nname: ledger\ndefinition: d\nkind: data\nevidence:\n  mode: any\n  quotes:\n"
                f"  - source: {SRC}#seg-02\n    text: Accounts are billed monthly.\n"
                f"  - source: {SRC}#seg-02\n    text: The ledger is billed monthly.\n")
    issues = run(entity_gate, {"kb/glossary/TERM-ledger.yaml": any_mode})
    assert codes_of(issues) == [("entity.name_not_in_quote", "warning", "TERM-ledger"), ("quote.not_found", "warning", "TERM-ledger")]


def test_drafted_term_without_evidence_is_unquoted():
    by = "drafted_by: agent:test via kb ingest brief v1\n"
    issues = run(entity_gate, {"kb/glossary/TERM-invoice.yaml": term("TERM-invoice", "invoice", kind=None) + by})
    assert codes_of(issues) == [("entity.unquoted", "warning", "TERM-invoice")]
    assert run(entity_gate, {"kb/glossary/TERM-invoice.yaml": term("TERM-invoice", "invoice", kind=None)}) == []


def test_drafted_alias_and_unquoted_errors_are_rejected_in_report(tmp_path):
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": CONFIG, "docs/spec.md": DOC})
    ok(root, "ingest")
    git(root, "add", "-A")
    git(root, "commit", "-m", "ingest")
    by = "drafted_by: agent:test via kb ingest brief v1\n"
    write(root, "kb/glossary/TERM-account.yaml", account() + by)
    write(root, "kb/glossary/TERM-invoice.yaml", term("TERM-invoice", "invoice", kind=None) + by)
    write(root, "kb/glossary/TERM-ledger.yaml",
          term("TERM-ledger", "ledger", aliases=["a", "account"], quote="An account is a customer of Acme.") + by)
    r = ingest_report(root, "HEAD")
    assert sorted((i.record, i.code, i.severity) for i in r.rejected) == [
        ("TERM-account", "entity.alias_conflict", "error"),
        ("TERM-invoice", "entity.unquoted", "error"),
        ("TERM-ledger", "entity.alias_conflict", "error"),
        ("TERM-ledger", "entity.alias_invalid", "error"),
    ]
    git(root, "add", "-A")
    git(root, "commit", "-m", "terms")
    out = invoke(root, "check")
    assert out.exit_code == 0 and "entity.alias_invalid" in out.output and "entity.unquoted" in out.output
