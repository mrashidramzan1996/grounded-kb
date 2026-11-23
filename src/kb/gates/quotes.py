import re

from kb.gates.base import GateContext
from kb.issues import Issue
from kb.quotes.match import find_quote
from kb.records.lifecycle import live_ids
from kb.records.models import Decision, Invariant, Requirement

MAX_QUOTES_PER_CLAIM = 5
_NUMBER = re.compile(r"\d[\d,.]*")
_ID_LIKE = re.compile(r"\b[A-Z]+(?:-[A-Z]+)*-\d+\b|\bseg-\d+\b")  # REQ-F-001, ADR-0002, seg-03: not numbers


def _numbers(text: str) -> set[str]:
    """Number tokens with thousands separators and trailing punctuation removed (units are optional)."""
    return {m.group().replace(",", "").rstrip(".") for m in _NUMBER.finditer(_ID_LIKE.sub(" ", text))}


def _claimed_text(rec: Requirement | Decision | Invariant) -> str:
    """What the record asserts, whose numbers its quotes must contain."""
    return f"{rec.title}\n{rec.body}" if isinstance(rec, Decision) else rec.statement


def missing_evidence(rid: str, rec: Decision | Invariant, severity: str, path: str) -> Issue:
    kind = "decision" if isinstance(rec, Decision) else "invariant"
    return Issue("evidence.missing", f"{rid}: this {kind} quotes no source; add evidence.quotes from the document it comes from",
                 severity, path, rid)


def quote_gate(ctx: GateContext) -> list[Issue]:
    repo, issues = ctx.repo, []
    live = live_ids(repo)
    for rid, rec in sorted(repo.records.items()):
        if not isinstance(rec, (Requirement, Decision, Invariant)):
            continue
        path = repo.paths[rid]
        ev = rec.evidence
        if isinstance(rec, Requirement) and "vague" in rec.flags:
            issues.append(Issue("requirement.vague", f"{rid} is flagged vague", "warning", path, rid))
        if ev is None:
            if isinstance(rec, (Decision, Invariant)) and rid in live:
                # agent-drafted: an error; a human may record a decision's rationale without a document
                severity = "error" if rec.drafted_by else "warning"
                issues.append(missing_evidence(rid, rec, severity, path))
            continue
        if len(ev.quotes) > MAX_QUOTES_PER_CLAIM:
            issues.append(Issue("quote.too_many", f"{rid} has {len(ev.quotes)} quotes; at most {MAX_QUOTES_PER_CLAIM} are allowed", path=path, record=rid))
        for q in ev.quotes:
            src, _, seg_id = q.source.partition("#")
            seg = next((s for s in repo.segments(src) if s.id == seg_id), None)
            if src not in repo.sources or seg is None:
                continue  # id_gate reports the missing source or segment
            if find_quote(repo.source_text(src)[seg.start : seg.end], q.text) is None:
                issues.append(Issue("quote.not_found", f"{rid}: quote not found in {q.source}: {q.text!r}", path=path, record=rid))
        grounded = set().union(*(_numbers(q.text) for q in ev.quotes))
        missing = sorted(_numbers(_claimed_text(rec)) - grounded)
        if missing:
            what = "title and body" if isinstance(rec, Decision) else "statement"
            issues.append(Issue("statement.number_not_in_quote", f"{rid}: {what} numbers not in any quote: {', '.join(missing)}", "warning", path, rid))
    return issues
