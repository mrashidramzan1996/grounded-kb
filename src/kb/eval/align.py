"""Deterministic alignment of the kb records with a blind second extraction (run B).

Records align by quote-span overlap, entities (glossary terms) by name and alias, relations by (source, type, target).
Both sides' spans come from gates.quotes.located_quotes, so the quote gate's rules decide what a span is.
"""

from dataclasses import dataclass, field
from typing import NamedTuple

from kb.gates.coverage import COVERING_TYPES, quote_spans
from kb.gates.entities import normalise_name
from kb.gates.quotes import located_quotes
from kb.graph import entity_key
from kb.records.lifecycle import live_ids
from kb.records.models import (
    Decision,
    Evidence,
    Exclusion,
    Invariant,
    Requirement,
    Term,
)
from kb.records.repo import KbRepo

Span = tuple[str, int, int]  # (source id, start, end) in the source's text.md
MIN_OVERLAP = 0.5  # overlap / the shorter item's quoted length, in some shared source

_KINDS: dict[type, str] = {Requirement: "requirement", Exclusion: "exclusion", Decision: "decision", Invariant: "invariant"}


class Link(NamedTuple):
    """A relation of an entity item: its type, the target item's id, and its quote text and segment ref."""

    type: str
    target: str
    quote: str
    ref: str


class RelationQuote(NamedTuple):
    """An unmatched run B relation as the judge is asked about it: both ends' names, and its quote and segment ref."""

    source_names: tuple[str, ...]
    target_names: tuple[str, ...]
    quote: str
    ref: str


@dataclass(frozen=True)
class Item:
    """A record or an entity (kind "entity") of one run. `spans` are its found quotes; an entity also has its
    normalised names (the name first) and its relations."""

    id: str
    kind: str  # requirement, decision, invariant, exclusion, or entity
    statement: str
    spans: tuple[Span, ...]
    names: tuple[str, ...] = ()
    relations: tuple[Link, ...] = ()


@dataclass(frozen=True)
class Alignment:
    matched: list[tuple[str, str]]  # (kb id, B id): every pair whose spans overlap enough
    only_kb: list[str]
    only_b: list[Item]
    splits: list[tuple[str, list[str]]]  # one kb record against several B items
    merges: list[tuple[list[str], str]]  # several kb records against one B item
    matched_entities: list[tuple[str, str]] = field(default_factory=list)  # (kb term id, B term id)
    only_kb_entities: list[str] = field(default_factory=list)
    only_b_entities: list[Item] = field(default_factory=list)
    matched_relations: list[tuple[str, str]] = field(default_factory=list)  # ("TERM-a:owns:TERM-b", "B-TERM-x:owns:B-TERM-y")
    only_kb_relations: list[str] = field(default_factory=list)  # "TERM-a:owns:TERM-b"
    # (source name, type, target name), one per distinct unmatched relation: two can show the same names
    only_b_relations: list[tuple[str, str, str]] = field(default_factory=list)
    only_b_relation_quotes: list[RelationQuote] = field(default_factory=list)  # one per only_b_relations entry, same order
    unlocated: list[str] = field(default_factory=list)  # kb records none of whose quotes is found: not aligned at all


# --- items -------------------------------------------------------------------------------------------------------

def entity_names(term: Term) -> tuple[str, ...]:
    """The names a term aligns by: graph.entity_key (usable name and aliases), the name first. When none is usable the
    result is empty and the term never aligns: an unusable name ("it", ".") is never matched on."""
    key = entity_key(term)
    return tuple(n for n in dict.fromkeys(normalise_name(x) for x in (term.name, *term.aliases)) if n in key)


def found_spans(repo: KbRepo, ev: Evidence | None) -> tuple[Span, ...]:
    """The spans of `ev`'s found quotes (gates.quotes.located_quotes), sorted."""
    return tuple(sorted((src, a, b) for _, src, a, b in located_quotes(repo, ev))) if ev is not None else ()


def record_item(iid: str, rec: Requirement | Exclusion | Decision | Invariant, spans: tuple[Span, ...]) -> Item:
    statement = rec.title if isinstance(rec, Decision) else rec.statement
    return Item(iid, _KINDS[type(rec)], statement, spans)


def entity_item(iid: str, term: Term, spans: tuple[Span, ...], target_prefix: str = "") -> Item:
    """`target_prefix` is put before every relation target: run B's terms are named B-TERM-… as items."""
    links = tuple(Link(r.type, target_prefix + r.target, r.quote.text, r.quote.source) for r in term.relations)
    return Item(iid, "entity", term.definition, spans, entity_names(term), links)


def kb_items(repo: KbRepo) -> list[Item]:
    """Live requirements, exclusions, decisions and invariants with evidence (spans from coverage.quote_spans), and
    live glossary terms as entities, sorted by id. A record none of whose quotes is found has no spans; align() lists
    it as unlocated."""
    live = live_ids(repo)
    spans: dict[str, list[Span]] = {}
    for src, found in quote_spans(repo).items():
        for a, b, rid in found:
            spans.setdefault(rid, []).append((src, a, b))
    out: list[Item] = []
    for rid, rec in sorted(repo.records.items()):
        if rid not in live:
            continue
        if isinstance(rec, COVERING_TYPES) and rec.evidence is not None:
            out.append(record_item(rid, rec, tuple(sorted(spans.get(rid, [])))))
        elif isinstance(rec, Term):
            out.append(entity_item(rid, rec, found_spans(repo, rec.evidence)))
    return out


# --- alignment ---------------------------------------------------------------------------------------------------

def _merged(spans: tuple[Span, ...]) -> dict[str, list[tuple[int, int]]]:
    """Per source, the spans as sorted, non-overlapping intervals."""
    out: dict[str, list[tuple[int, int]]] = {}
    for src, a, b in sorted(spans):
        if b <= a:
            continue
        cur = out.setdefault(src, [])
        if cur and a <= cur[-1][1]:
            cur[-1] = (cur[-1][0], max(cur[-1][1], b))
        else:
            cur.append((a, b))
    return out


def _length(iv: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in iv)


def _overlap(x: list[tuple[int, int]], y: list[tuple[int, int]]) -> int:
    total = i = j = 0
    while i < len(x) and j < len(y):
        total += max(0, min(x[i][1], y[j][1]) - max(x[i][0], y[j][0]))
        if x[i][1] <= y[j][1]:
            i += 1
        else:
            j += 1
    return total


def _overlaps(a: dict[str, list[tuple[int, int]]], b: dict[str, list[tuple[int, int]]]) -> bool:
    for src in a.keys() & b.keys():
        shorter = min(_length(a[src]), _length(b[src]))
        if shorter and _overlap(a[src], b[src]) / shorter >= MIN_OVERLAP:
            return True
    return False


def _label(item: Item | None, fallback: str) -> str:
    """How an entity is named in a relation key: its first name, else its id."""
    return item.names[0] if item is not None and item.names else fallback


def _relations(items: list[Item]) -> list[tuple[str, frozenset[str], str, frozenset[str], str, str, RelationQuote]]:
    """(label "src:type:target", source names, type, target names, source name, target name, RelationQuote) per
    relation; an end that is not among `items`, or has no names, has an empty key and never matches."""
    by_id = {i.id: i for i in items}
    out = []
    for item in items:
        for r in item.relations:
            target = by_id.get(r.target)
            quote = RelationQuote(item.names, target.names if target else (), r.quote, r.ref)
            out.append((f"{item.id}:{r.type}:{r.target}", frozenset(item.names), r.type,
                        frozenset(target.names) if target else frozenset(), _label(item, item.id), _label(target, r.target), quote))
    return out


def align(kb_items: list[Item], b_items: list[Item]) -> Alignment:
    """Records match when, in some shared source, their found quotes overlap by at least MIN_OVERLAP of the shorter
    one; every such pair is matched (one record may match several). A kb record without spans is unlocated, not
    aligned. Entities match when their names share one; relations when the type is the same and both ends' names
    share one."""
    unlocated = sorted(i.id for i in kb_items if i.kind != "entity" and not i.spans)
    kb_recs = [i for i in kb_items if i.kind != "entity" and i.spans]
    b_recs = [i for i in b_items if i.kind != "entity"]
    kb_spans = [(k.id, _merged(k.spans)) for k in kb_recs]
    b_spans = [(b.id, _merged(b.spans)) for b in b_recs]
    matched = sorted((k, b) for k, mk in kb_spans for b, mb in b_spans if _overlaps(mk, mb))
    per_kb: dict[str, list[str]] = {}
    per_b: dict[str, list[str]] = {}
    for k, b in matched:
        per_kb.setdefault(k, []).append(b)
        per_b.setdefault(b, []).append(k)

    kb_ents = [i for i in kb_items if i.kind == "entity"]
    b_ents = [i for i in b_items if i.kind == "entity"]
    matched_e = sorted((k.id, b.id) for k in kb_ents for b in b_ents if set(k.names) & set(b.names))
    kb_e_hit, b_e_hit = {k for k, _ in matched_e}, {b for _, b in matched_e}

    kb_rels, b_rels = _relations(kb_ents), _relations(b_ents)
    matched_r = sorted({(k[0], b[0]) for k in kb_rels for b in b_rels if k[2] == b[2] and k[1] & b[1] and k[3] & b[3]})
    kb_r_hit, b_r_hit = {k for k, _ in matched_r}, {b for _, b in matched_r}
    only_b_r = {b[0]: b for b in b_rels if b[0] not in b_r_hit}  # by id label: names may repeat
    only_b_sorted = [b for _, b in sorted(only_b_r.items(), key=lambda kv: ((kv[1][4], kv[1][2], kv[1][5]), kv[0]))]

    return Alignment(
        matched=matched,
        only_kb=sorted(k.id for k in kb_recs if k.id not in per_kb),
        only_b=sorted((b for b in b_recs if b.id not in per_b), key=lambda i: i.id),
        splits=sorted((k, sorted(bs)) for k, bs in per_kb.items() if len(bs) >= 2),
        merges=sorted(((sorted(ks), b) for b, ks in per_b.items() if len(ks) >= 2), key=lambda m: m[1]),
        matched_entities=matched_e,
        only_kb_entities=sorted(k.id for k in kb_ents if k.id not in kb_e_hit),
        only_b_entities=sorted((b for b in b_ents if b.id not in b_e_hit), key=lambda i: i.id),
        matched_relations=matched_r,
        only_kb_relations=sorted({k[0] for k in kb_rels if k[0] not in kb_r_hit}),
        only_b_relations=[(b[4], b[2], b[5]) for b in only_b_sorted],
        only_b_relation_quotes=[b[6] for b in only_b_sorted],
        unlocated=unlocated,
    )


# --- agreement ---------------------------------------------------------------------------------------------------

def _facts(pairs: list[tuple[str, str]]) -> int:
    """Groups of matched items: connected components of the match pairs, so a split or a merge counts once."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, b in pairs:
        parent[find("kb:" + k)] = find("b:" + b)
    return len({find(x) for x in parent})


def _side_f1(pairs: list[tuple[str, str]], only_kb: list, only_b: list) -> float | None:
    """The share of both sides' items that found a partner (2m/(n_kb+n_b) when matches are one-to-one)."""
    kb, b = len({k for k, _ in pairs}), len({x for _, x in pairs})
    total = kb + len(only_kb) + b + len(only_b)
    return (kb + b) / total if total else None


def _round_half_up(x: float) -> int:
    return int(x + 0.5)


def agreement(al: Alignment, n_kb: int, n_b: int) -> dict:
    """Agreement of the two runs, counted in facts: `matched` facts both runs found (a split or a merge is one fact),
    plus each run's unmatched items, so a pure split is full agreement. `kb_items` and `b_items` are the raw item counts
    `n_kb` and `n_b`; `unlocated` kb records (no quote found) take no part. `estimated_total` is the capture–recapture
    (Lincoln–Petersen) estimate of how many facts there are, rounded half up; both runs tend to miss the same ones, so
    the kb's facts / estimated_total is an upper bound on recall."""
    m = _facts(al.matched)
    kb_facts, b_facts = m + len(al.only_kb), m + len(al.only_b)
    return {
        "matched": m,
        "only_kb": len(al.only_kb),
        "only_b": len(al.only_b),
        "kb_items": n_kb,
        "b_items": n_b,
        "unlocated": len(al.unlocated),
        "span_f1": 2 * m / (kb_facts + b_facts) if kb_facts + b_facts else None,
        "estimated_total": _round_half_up(kb_facts * b_facts / m) if m else None,
        "entity_f1": _side_f1(al.matched_entities, al.only_kb_entities, al.only_b_entities),
        "relation_f1": _side_f1(al.matched_relations, al.only_kb_relations, al.only_b_relations),
    }


def _ratio(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def render_agreement(agr: dict) -> str:
    """Markdown lines for an agreement dict; the capture–recapture estimate is always shown as an upper bound."""
    kb_facts = agr["matched"] + agr["only_kb"]
    lines = [
        f"- matched: {agr['matched']}; only in kb: {agr['only_kb']}; only in run B: {agr['only_b']}",
        f"- span F1: {_ratio(agr['span_f1'])}; entity F1: {_ratio(agr['entity_f1'])}; relation F1: {_ratio(agr['relation_f1'])}",
    ]
    est = agr["estimated_total"]
    if est:
        lines.append(f"- capture–recapture estimate: about {est} in all, so kb holds at most {kb_facts} of {est} "
                     f"({_round_half_up(100 * kb_facts / est)}%). This is an upper bound on recall: both runs tend to miss the same rules.")
    return "\n".join(lines) + "\n"
