"""Sentence-level coverage: every rule-like sentence of a stored document should be quoted by a record (0.4.0)."""

import re

from kb.gates.base import GateContext
from kb.gates.quotes import located_quotes
from kb.gates.sources import bad_segment_ids
from kb.issues import Issue
from kb.records.lifecycle import live_ids
from kb.records.models import Decision, Exclusion, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.sources.sentences import (
    Sentence,
    is_normative,
    same_sentence,
    sentence_key,
    sentences,
)
from kb.textfmt import inline_code

# the record types whose quotes cover a sentence; entity and relation quotes never do
COVERING_TYPES: tuple[type, ...] = (Requirement, Exclusion, Decision, Invariant)
Span = tuple[int, int, str]  # [start, end) in the source's text.md, and the record id


def quote_spans(repo: KbRepo) -> dict[str, list[Span]]:
    """{source id: (start, end, record id)} for each found quote (gates.quotes.located_quotes: the quote gate's rules)
    of a live covering record, sorted by position."""
    live = live_ids(repo)
    out: dict[str, list[Span]] = {}
    for rid, rec in sorted(repo.records.items()):
        if isinstance(rec, COVERING_TYPES) and rid in live and rec.evidence is not None:
            for _, src, start, end in located_quotes(repo, rec.evidence):
                out.setdefault(src, []).append((start, end, rid))
    return {src: sorted(spans) for src, spans in out.items()}


def _overlaps(s: Sentence, start: int, end: int) -> bool:
    return max(s.start, start) < min(s.end, end)


_DECIDED_LINE = re.compile(r"Decided by \S+ on \S+\.")


def _clarify_boilerplate() -> set[str]:
    """The sentences `kb clarify` writes itself: the header of docs/clarifications.md."""
    from kb.clarify import _HEADER

    return {" ".join(t.split()) for t in re.split(r"(?<=[.!?])\s+", _HEADER.partition("\n\n")[2].strip())}


def _kb_written(src: str, s: Sentence, boilerplate: set[str]) -> bool:
    """A sentence of the clarifications source that kb wrote (its header, a `Decided by ... on ....` line), not a person."""
    flat = " ".join(s.text.split())
    return src == "SRC-clarifications" and (flat in boilerplate or bool(_DECIDED_LINE.fullmatch(flat)))


def _normative(repo: KbRepo) -> dict[str, list[Sentence]]:
    """Rule-like sentences by source; a source whose segment ids are invalid is skipped (the source gate reports it)."""
    bad = set(bad_segment_ids(repo))
    boilerplate = _clarify_boilerplate()
    return {src: [s for s in sentences(repo, src) if is_normative(s) and not _kb_written(src, s, boilerplate)]
            for src in sorted(set(repo.sources) - bad)}


def _uncovered(rules: dict[str, list[Sentence]], spans: dict[str, list[Span]], decided) -> list[Sentence]:
    return [
        s
        for src, found in rules.items()
        for s in found
        if sentence_key(s) not in decided and not any(_overlaps(s, a, b) for a, b, _ in spans.get(src, ()))
    ]


def uncovered(repo: KbRepo, decided: set[str] | frozenset[str] = frozenset()) -> list[Sentence]:
    """The rule-like sentences no quote span overlaps, minus those whose sentence_key is in `decided`."""
    return _uncovered(_normative(repo), quote_spans(repo), decided)


def _quoted_chars(spans: list[Span]) -> int:
    """Characters inside at least one span (overlaps counted once)."""
    total, reach = 0, -1
    for a, b, _ in spans:
        if b > reach:
            total += b - max(a, reach)
            reach = b
    return total


def coverage_stats(repo: KbRepo) -> dict:
    """Coverage of rule-like sentences, next to the share of the text that is quoted at all: quoting whole
    segments raises coverage but shows up as a high quoted share. With no rule-like sentence the ratio is 1.0."""
    rules, spans = _normative(repo), quote_spans(repo)
    normative = sum(len(v) for v in rules.values())
    covered = normative - len(_uncovered(rules, spans, frozenset()))
    return {
        "normative": normative,
        "covered": covered,
        "ratio": covered / normative if normative else 1.0,
        "quoted_chars": sum(_quoted_chars(spans.get(src, [])) for src in rules),
        "text_chars": sum(len(repo.source_text(src)) for src in rules),
    }


def coverage_gate(ctx: GateContext) -> list[Issue]:
    """sentence.uncovered leaves out the sentences a person decided (kb/eval/decisions.yaml as `ctx.repo` holds it:
    added, covered or not a rule); eval.decisions_invalid (error) for each decision there that does not stand."""
    # imported here: kb.eval imports this module
    from kb.eval.decisions import decisions_of, suppressed_keys

    repo = ctx.repo
    rules, spans = _normative(repo), quote_spans(repo)
    decisions, invalid = decisions_of(repo, ctx.root)  # an entry that does not stand settles nothing, and is an error
    issues: list[Issue] = list(invalid)
    for s in _uncovered(rules, spans, suppressed_keys(decisions)):
        src = s.ref.partition("#")[0]
        issues.append(Issue("sentence.uncovered", f"{s.ref}: rule-like sentence no record quotes: {inline_code(s.text)}",
                            "warning", f"kb/sources/{src}/triage.yaml", src))
    merged: dict[str, int] = {}
    for src, found in spans.items():
        text = repo.source_text(src) if found else ""
        for a, b, rid in found:
            n = 0  # rule-like sentences, not clauses: the clauses of one sentence ("...; it must...") count once
            last = None
            for s in rules.get(src, ()):
                if _overlaps(s, a, b):
                    n += 0 if last is not None and same_sentence(text, last, s) else 1
                    last = s
            if n >= 2:
                merged[rid] = max(merged.get(rid, 0), n)
    for rid, n in sorted(merged.items()):
        issues.append(Issue("quote.multiple_rules", f"{rid}: its quote spans {n} rule-like sentences; split it into one record per rule",
                            "warning", repo.paths[rid], rid))
    for src, found in rules.items():
        tri = repo.triage(src)
        for seg_id, value in sorted(tri.segments.items()) if tri else ():
            if not isinstance(value, str):
                continue
            ref = f"{src}#{seg_id}"
            held = [s for s in found if s.ref == ref]
            if held:
                issues.append(Issue("triage.none_has_rules", f"{ref}: triaged {inline_code(value)} but holds {len(held)} rule-like "
                                    f"sentence(s), such as {inline_code(held[0].text)}", "warning", f"kb/sources/{src}/triage.yaml", src))
    return issues
