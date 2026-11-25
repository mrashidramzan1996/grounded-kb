import re
from collections.abc import Iterable

from kb.gates.base import GateContext
from kb.issues import Issue
from kb.quotes.match import find_quote
from kb.records.lifecycle import live_ids
from kb.records.models import (
    QUOTED_TYPES,
    Decision,
    Evidence,
    Exclusion,
    Feature,
    Invariant,
    Quote,
    Requirement,
)
from kb.records.repo import KbRepo
from kb.sources.sentences import cues
from kb.textfmt import inline_code

MAX_QUOTES_PER_CLAIM = 5
_NUMBER = re.compile(r"\d[\d,.]*")
MIN_QUOTE_CHARS = 12  # non-space characters: shorter quotes match almost anywhere
# kb's own ids (REQ-F-001, ADR-0002, seg-03) are not numbers; "ISO-27001" is
_ID_LIKE = re.compile(r"\b(?:REQ-F|REQ-NF|FEAT|ADR|INV|EXCL|COMP|TERM)-\d+\b|\bseg-\d+\b")


def numbers(text: str) -> set[str]:
    """Number tokens with thousands separators and trailing punctuation removed (units are optional)."""
    return {m.group().replace(",", "").rstrip(".") for m in _NUMBER.finditer(_ID_LIKE.sub(" ", text))}


def _claimed_text(rec: Requirement | Exclusion | Decision | Invariant) -> str:
    """What the record asserts, whose numbers its quotes must contain."""
    return f"{rec.title}\n{rec.body}" if isinstance(rec, Decision) else rec.statement


def _grounded(repo: KbRepo, rids: Iterable[str]) -> set[str]:
    """Numbers in the linked requirements' statements and quote texts."""
    out: set[str] = set()
    for rid in rids:
        rec = repo.records.get(rid)
        if isinstance(rec, Requirement):
            out |= numbers(rec.statement)
            for q in rec.evidence.quotes:
                out |= numbers(q.text)
    return out


def feature_number_issues(repo: KbRepo) -> list[Issue]:
    """Numbers in a live feature's claims, title and body must appear in the requirements it links."""
    issues: list[Issue] = []
    live = live_ids(repo)
    for fid, rec in sorted(repo.records.items()):
        if not isinstance(rec, Feature) or fid not in live:
            continue
        path = repo.paths[fid]
        for claim in rec.claims:
            linked = [claim.requirement] if claim.requirement else list(rec.requirements)
            missing = sorted(numbers(claim.claim) - _grounded(repo, linked))
            if missing:
                where = ", ".join(linked) if linked else "any linked requirement"
                issues.append(Issue("claim.number_not_in_requirement", f"{claim.id}: claim numbers not in {where}: {', '.join(missing)}",
                                    "warning", path, fid))
        linked = list(dict.fromkeys([*rec.requirements, *(c.requirement for c in rec.claims if c.requirement)]))
        missing = sorted(numbers(f"{rec.title}\n{rec.body}") - _grounded(repo, linked))
        if missing:
            issues.append(Issue("claim.number_not_in_requirement",
                                f"{fid}: title and body numbers not in any linked requirement: {', '.join(missing)}", "warning", path, fid))
    return issues


def missing_evidence(rid: str, rec: Decision | Invariant, severity: str, path: str) -> Issue:
    kind = "decision" if isinstance(rec, Decision) else "invariant"
    return Issue("evidence.missing", f"{rid}: this {kind} quotes no source; add evidence.quotes from the document it comes from",
                 severity, path, rid)


def too_short(text: str) -> bool:
    return len("".join(text.split())) < MIN_QUOTE_CHARS


def located_quotes(repo: KbRepo, evidence: Evidence) -> list[tuple[Quote, str, int, int]]:
    """(quote, source id, start, end) for each found quote: long enough and verbatim, aligned to word and number
    boundaries, in its segment. start and end are offsets into the source's text.md. A quote whose source or segment
    is missing is not found."""
    out: list[tuple[Quote, str, int, int]] = []
    for q in evidence.quotes:
        src, _, seg_id = q.source.partition("#")
        seg = next((s for s in repo.segments(src) if s.id == seg_id), None) if src in repo.sources else None
        if seg is None or too_short(q.text):
            continue
        at = find_quote(repo.source_text(src)[seg.start : seg.end], q.text)
        if at is not None:
            out.append((q, src, seg.start + at[0], seg.start + at[1]))
    return out


def quote_results(repo: KbRepo, evidence: Evidence) -> tuple[list[Quote], list[Quote]]:
    """(found, unfound) quotes, by located_quotes. Under `mode: any` a record is unfound only when `found` is empty."""
    located = [q for q, *_ in located_quotes(repo, evidence)]
    return [q for q in evidence.quotes if q in located], [q for q in evidence.quotes if q not in located]


_MODALITY_CUES = {"must": "must", "must_not": "must_not", "should": "should", "may": "may"}


def requirement_field_issues(rid: str, rec: Requirement, found: list[Quote], path: str) -> list[Issue]:
    """The classification fields against the record's found quotes."""
    issues: list[Issue] = []
    missing = [f for f in ("modality", "actor") if getattr(rec, f) is None]
    if rec.id.startswith("REQ-NF-") and rec.category is None:
        missing.append("category")
    if missing:
        issues.append(Issue("requirement.fields_missing", f"{rid}: missing {', '.join(missing)}; set them from the quote", "warning", path, rid))
    # only a quote that exists confirms an implied requirement: an unfound one proves nothing
    if rec.origin == "implied" and not any(q.source.partition("#")[0] == "SRC-clarifications" for q in found):
        issues.append(Issue("requirement.implied_unconfirmed",
                            f"{rid}: origin is implied and no found quote cites SRC-clarifications; a person must confirm it", "warning", path, rid))
    seen = {_MODALITY_CUES[c] for q in found for c in cues(q.text) if c in _MODALITY_CUES}
    if seen and rec.modality is not None and rec.modality not in seen:
        issues.append(Issue("requirement.modality_mismatch",
                            f"{rid}: modality {rec.modality} but the quote says {', '.join(sorted(seen))}", "warning", path, rid))
    return issues


def evidence_issues(repo: KbRepo, rid: str, ev: Evidence, found: list[Quote], live: bool, path: str) -> list[Issue]:
    """quote.too_many, quote.too_short and quote.not_found for one record's evidence; `found` is quote_results'."""
    issues: list[Issue] = []
    if len(ev.quotes) > MAX_QUOTES_PER_CLAIM:
        issues.append(Issue("quote.too_many", f"{rid} has {len(ev.quotes)} quotes; at most {MAX_QUOTES_PER_CLAIM} are allowed", path=path, record=rid))
    unfound: list[Issue] = []
    for q in ev.quotes:
        src, _, seg_id = q.source.partition("#")
        seg = next((s for s in repo.segments(src) if s.id == seg_id), None)
        if too_short(q.text):
            issues.append(Issue("quote.too_short", f"{rid}: quote shorter than {MIN_QUOTE_CHARS} characters: {inline_code(q.text)}", "warning", path, rid))
            continue
        if src not in repo.sources or seg is None:
            continue  # id_gate reports the missing source or segment
        if q in found:
            continue
        if live:
            unfound.append(Issue("quote.not_found", f"{rid}: quote not found in {q.source}: {inline_code(q.text)}", path=path, record=rid))
        else:  # a superseded or rejected record stays as it was, even when its document moved on
            issues.append(Issue("quote.not_found", f"{rid}: quote no longer in the current source ({q.source}); the record is superseded or rejected",
                                "warning", path, rid))
    if unfound and ev.mode == "any" and found:  # one grounded quote is enough; the others are only noted
        unfound = [Issue(i.code, i.message, "warning", i.path, i.record) for i in unfound]
    return issues + unfound


def quote_gate(ctx: GateContext) -> list[Issue]:
    repo, issues = ctx.repo, []
    live = live_ids(repo)
    issues.extend(feature_number_issues(repo))
    for rid, rec in sorted(repo.records.items()):
        if not isinstance(rec, QUOTED_TYPES):
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
        found, _ = quote_results(repo, ev)
        if isinstance(rec, Requirement):
            issues.extend(requirement_field_issues(rid, rec, found, path))
        issues.extend(evidence_issues(repo, rid, ev, found, rid in live, path))
        grounded = set().union(*(numbers(q.text) for q in found))  # only quotes that exist ground a number
        missing = sorted(numbers(_claimed_text(rec)) - grounded)
        if missing:
            what = "title and body" if isinstance(rec, Decision) else "statement"
            issues.append(Issue("statement.number_not_in_quote", f"{rid}: {what} numbers not in any quote: {', '.join(missing)}", "warning", path, rid))
    return issues
