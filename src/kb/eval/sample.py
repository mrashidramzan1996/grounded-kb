"""`kb eval sample`: a deterministic QC sample for a person to check by hand, as a Markdown checklist.

Seeded by the HEAD commit, so the same commit always gives the same sample (a reviewer can reproduce it) and a new
commit a new one. It shows records with their quotes and sentences no record quotes, never a judge verdict: the
person checks the extraction, not the evaluation agent.
"""

import random

from kb.gates.coverage import quote_spans
from kb.gates.quotes import located_quotes
from kb.gates.sources import bad_segment_ids
from kb.records.lifecycle import live_ids
from kb.records.models import Decision, Exclusion, Invariant, Requirement, Term
from kb.records.repo import KbRepo
from kb.sources.sentences import Sentence, is_normative, sentences
from kb.textfmt import inline_code

DEFAULT_N = 10


def _gist(rec) -> str:
    """What a record says, for the person to compare with its quote."""
    if isinstance(rec, (Requirement, Exclusion, Invariant)):
        return rec.statement
    if isinstance(rec, Term):
        return f"{rec.name}: {rec.definition}"
    if isinstance(rec, Decision):
        return rec.title
    return ""


def _quoted_records(repo: KbRepo) -> list[str]:
    """Live records with at least one quote, by id."""
    live = live_ids(repo)
    return sorted(rid for rid, rec in repo.records.items()
                  if rid in live and getattr(rec, "evidence", None) is not None and rec.evidence.quotes)


def _unquoted(repo: KbRepo) -> tuple[list[Sentence], list[Sentence]]:
    """(rule-like, other) sentences no quote of a live requirement, exclusion, decision or invariant overlaps, in
    document order."""
    spans = quote_spans(repo)
    bad = set(bad_segment_ids(repo))
    rules, other = [], []
    for src in sorted(set(repo.sources) - bad):
        for s in sentences(repo, src):
            if any(max(s.start, a) < min(s.end, b) for a, b, _ in spans.get(src, ())):
                continue
            (rules if is_normative(s) else other).append(s)
    return rules, other


def _pick(rng: random.Random, pool: list, k: int) -> list:
    return rng.sample(pool, min(k, len(pool)))


def _record_line(repo: KbRepo, rid: str) -> str:
    rec = repo.records[rid]
    located = located_quotes(repo, rec.evidence)
    q = located[0][0] if located else rec.evidence.quotes[0]
    return (f"- [ ] {inline_code(rid)}: {inline_code(_gist(rec))} · quote {inline_code(q.text)} ({inline_code(q.source)}) "
            "· says what the quote says, nothing more")


def _sentence_line(s: Sentence) -> str:
    what = "rule-like" if is_normative(s) else "not rule-like"
    return f"- [ ] {inline_code(s.ref)} · {what} · {inline_code(s.text)} · should a record quote it?"


def build_sample(repo: KbRepo, head: str, n: int = DEFAULT_N) -> str:
    """n live records (with their quotes) and n unquoted sentences, half rule-like and half not (the other pile fills
    in when one runs short), chosen by random.Random(int(head[:12], 16)). Markdown; untrusted text as inline code."""
    rng = random.Random(int(head[:12], 16))
    quoted = _quoted_records(repo)
    records = sorted(_pick(rng, quoted, n))
    rules, other = _unquoted(repo)
    want_rules = min(len(rules), max((n + 1) // 2, n - len(other)))
    picked_rules = _pick(rng, rules, want_rules)
    picked_other = _pick(rng, other, n - len(picked_rules))
    order = {id(s): i for i, s in enumerate([*rules, *other])}
    picked = sorted([*picked_rules, *picked_other], key=lambda s: order[id(s)])
    out = [
        "## Extraction QC sample",
        "",
        f"Seeded by commit `{head[:12]}` (`kb eval sample --n {n}`). Check each item against the document and tick it.",
        "",
        f"### Records ({len(records)} of {len(quoted)} quoted live records)",
        "",
        *([_record_line(repo, rid) for rid in records] or ["- none"]),
        "",
        (f"### Unquoted sentences ({len(picked_rules)} rule-like of {len(rules)}, {len(picked_other)} not rule-like of "
         f"{len(other)})"),
        "",
        *([_sentence_line(s) for s in picked] or ["- none"]),
    ]
    return "\n".join(out) + "\n"
