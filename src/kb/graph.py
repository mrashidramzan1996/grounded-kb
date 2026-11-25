"""`kb graph`: live records, modules and the links between them, as JSON or a Mermaid flowchart (0.4.0).

Also the entity helpers the extraction evaluation uses: `entity_key` (how a term is aligned across runs) and
`entity_candidates` (phrases the documents treat as named things that no live term covers yet).
"""

import re

from kb.config.schema import KbConfig
from kb.gates.entities import names, normalise_name
from kb.gates.sources import bad_segment_ids
from kb.quotes.match import fold
from kb.records.ids import (
    ADR_ID,
    CLAIM_ID,
    COMP_ID,
    EXCL_ID,
    FEAT_ID,
    INV_ID,
    REQ_ID,
    SOURCE_ID,
    SOURCE_REF,
    TERM_ID,
)
from kb.records.lifecycle import live_ids, supersede_edges
from kb.records.models import (
    Component,
    Decision,
    Exclusion,
    Feature,
    Invariant,
    Requirement,
    Term,
)
from kb.records.repo import KbRepo
from kb.sources.sentences import Sentence, sentences

MAX_LABEL = 60
_UNSAFE = re.compile(r'["\[\]|<>`\r\n]')


def _node(rec) -> tuple[str, str]:
    """(type, label) of a record."""
    if isinstance(rec, Requirement):
        return "requirement", rec.statement
    if isinstance(rec, Exclusion):
        return "exclusion", rec.statement
    if isinstance(rec, Feature):
        return "feature", rec.title
    if isinstance(rec, Decision):
        return "decision", rec.title
    if isinstance(rec, Invariant):
        return "invariant", rec.statement
    if isinstance(rec, Component):
        return "component", rec.title
    if isinstance(rec, Term):
        return "term", rec.name
    raise TypeError(f"no graph node for record type {type(rec).__name__}")


def build_graph(repo: KbRepo, config: KbConfig | None = None) -> dict:
    """{"nodes": [{id, type, label}], "edges": [{source, target, type, quote}]}, sorted. Nodes are the live records,
    the claims of live features, the records a live record supersedes, and the modules (from `config`, else the ones
    records name). Edges come from live records; an edge whose end is not a node (a missing or rejected record, an
    unknown module) is left out."""
    live = live_ids(repo)
    nodes: dict[str, tuple[str, str]] = {}
    for rid in live:
        nodes[rid] = _node(repo.records[rid])
    for target, supers in supersede_edges(repo).items():
        if any(s in live for s in supers):
            nodes[target] = _node(repo.records[target])
    for feature, claim in repo.claims():
        if feature.id in live:
            nodes[claim.id] = ("claim", claim.claim)

    edges: set[tuple[str, str, str, str | None]] = set()

    def add(source: str, target: str, type: str, quote: str | None = None) -> None:
        edges.add((source, target, type, quote))

    referenced: set[str] = set()
    for rid in sorted(live):
        rec = repo.records[rid]
        if isinstance(rec, (Requirement, Exclusion)):
            kind = "belongs_to" if isinstance(rec, Requirement) else "excludes"
            for target in [*rec.modules, *rec.features]:
                add(rid, target, kind)
            referenced.update(rec.modules)
        if isinstance(rec, Requirement):
            if rec.actor is not None and re.fullmatch(TERM_ID, rec.actor):
                add(rid, rec.actor, "actor")
            for e in rec.entities:
                add(rid, e, "mentions")
        if isinstance(rec, (Decision, Invariant)):
            for g in rec.governs:
                add(rid, g, "governs")
            referenced.update(g for g in rec.governs if g.startswith("MOD-"))
            if rec.supersedes:
                add(rid, rec.supersedes, "supersedes")
        if isinstance(rec, Feature):
            referenced.add(rec.module)
            for c in rec.claims:
                if c.requirement:
                    add(c.id, c.requirement, "implements")
        if isinstance(rec, Component):
            referenced.add(rec.module)
        if isinstance(rec, Term):
            for r in rec.relations:
                add(rid, r.target, r.type, r.quote.text)
    if config is not None:
        for m in config.modules:
            nodes[m.id] = ("module", m.title or m.id)
    else:
        for m in referenced:
            nodes[m] = ("module", m)

    return {
        "nodes": [{"id": i, "type": t, "label": label} for i, (t, label) in sorted(nodes.items())],
        "edges": [
            {"source": s, "target": t, "type": k, "quote": q}
            for s, t, k, q in sorted(edges, key=lambda e: (e[0], e[1], e[2], e[3] or ""))
            if s in nodes and t in nodes
        ],
    }


def _label(text: str) -> str:
    """Node text Mermaid cannot read as syntax: no quotes, brackets, pipes, angle brackets, backticks or line breaks;
    whitespace collapsed, at most MAX_LABEL characters."""
    return " ".join(_UNSAFE.sub("", text).split())[:MAX_LABEL].rstrip()


def render_mermaid(graph: dict) -> str:
    """A `flowchart LR`. Node ids are generated (n1, n2, … in node order), never record text; labels are `_label`ed."""
    ids = {n["id"]: f"n{i}" for i, n in enumerate(graph["nodes"], 1)}
    out = ["flowchart LR"]
    out += [f'    {ids[n["id"]]}["{_label(n["id"] + ": " + n["label"])}"]' for n in graph["nodes"]]
    out += [f'    {ids[e["source"]]} -->|{_label(e["type"])}| {ids[e["target"]]}' for e in graph["edges"]]
    return "\n".join(out) + "\n"


# --- entities for the extraction evaluation ----------------------------------------------------

def entity_key(term: Term) -> frozenset[str]:
    """The term's name and aliases, lower-cased with whitespace collapsed: how a term is aligned across two runs."""
    return frozenset(names(term))


_KB_ID = re.compile(
    rf"(?<![\w-])(?:{SOURCE_REF}|{SOURCE_ID}|{REQ_ID}|{CLAIM_ID}|{FEAT_ID}|{ADR_ID}|{INV_ID}|{EXCL_ID}|{COMP_ID}|{TERM_ID}"
    r"|MOD-[a-z0-9][a-z0-9-]*)(?![\w-])"
)
_ARTICLE = re.compile(r"(?:The|A|An) ")
_MARKER = re.compile(r"\s*(?:[-*+] |\d+[.)] |\| )?[\"'(*_]*")
_WORD = r"[A-Z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*"
_TITLE_RUN = re.compile(rf"(?<![\w-]){_WORD}(?: {_WORD})*(?![\w-])")
_DEFINED = (
    re.compile(r'"([^"]{2,80})" means\b'),
    re.compile(r'\(the "([^"]{2,80})"\)'),
)
_IS_A = re.compile(rf"((?:{_WORD} ){{0,4}}{_WORD}) is (?:a|an|the) ")
# sentence-start words that are never names
_NOT_NAMES = {"it", "this", "that", "there", "these", "those", "here", "what", "which", "who", "he", "she", "they", "we",
              "you", "each", "every", "all", "any", "no", "one", "our", "your", "its", "their"}


def _titles(repo: KbRepo, src_ids: list[str]) -> set[str]:
    """Each document's title: the root heading of its first headed segment, and its file name. Other headings are not
    skipped (a glossary gives each term its own section); sentences() already leaves heading lines out."""
    out: set[str] = set()
    for src in src_ids:
        root = next((seg.heading.split(" > ")[0] for seg in repo.segments(src) if seg.heading.strip()), "")
        stem = repo.sources[src].original.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        out.update({normalise_name(root), normalise_name(re.sub(r"[-_]+", " ", stem))} - {""})
    return out


def _start(text: str) -> int:
    """Where the sentence's first word starts, after a list marker or opening punctuation."""
    return _MARKER.match(text).end()  # type: ignore[union-attr]


def _strip_article(phrase: str) -> str:
    m = _ARTICLE.match(phrase)
    return phrase[m.end():] if m else phrase


def entity_candidates(repo: KbRepo) -> list[tuple[str, str, Sentence]]:
    """(normalised phrase, segment ref, first sentence) for each phrase the documents treat as a named thing and no live
    term names (by name or alias), sorted by phrase. Deterministic; only three patterns: a quoted defined term
    (`"X" means`, `(the "X")`), `X is a|an|the …` at the start of a sentence, and a Title Case phrase of 2 to 5 words
    that occurs at least twice, not only at the start of a sentence. A one-word `X is a …` counts only when X occurs
    again elsewhere in the documents. Kb ids and document titles are skipped; heading lines are not sentences."""
    live = live_ids(repo)
    covered = {n for rid, rec in repo.records.items() if isinstance(rec, Term) and rid in live for n in names(rec)}
    src_ids = sorted(set(repo.sources) - set(bad_segment_ids(repo)))
    skipped = _titles(repo, src_ids)
    first: dict[str, Sentence] = {}  # phrase -> first sentence it was seen in
    definite: set[str] = set()  # phrases from the first two patterns
    seen: dict[str, list[bool]] = {}  # Title Case phrase -> per occurrence, whether at the start of the sentence
    single: dict[str, str] = {}  # one-word `X is a` phrase -> X as written
    corpus: list[str] = []  # every sentence, folded, kb ids blanked

    def note(phrase: str, s: Sentence) -> str | None:
        key = normalise_name(phrase)
        if not key or key in skipped or key in covered or key in _NOT_NAMES:
            return None
        first.setdefault(key, s)
        return key

    for src in src_ids:
        for s in sentences(repo, src):
            text = _KB_ID.sub("  ", fold(s.text)[0])
            corpus.append(text)
            start = _start(text)
            for pattern in _DEFINED:
                for m in pattern.finditer(text):
                    if (key := note(m.group(1), s)) is not None:
                        definite.add(key)
            if (m := _IS_A.match(text, start)) is not None:
                phrase = _strip_article(m.group(1))
                if phrase and (key := note(phrase, s)) is not None:
                    if " " in phrase:
                        definite.add(key)
                    else:
                        single.setdefault(key, phrase)
            for m in _TITLE_RUN.finditer(text):
                phrase = _strip_article(m.group())
                if 2 <= len(phrase.split()) <= 5 and (key := note(phrase, s)) is not None:
                    seen.setdefault(key, []).append(m.start() == start)
    repeated = {k for k, starts in seen.items() if len(starts) >= 2 and not all(starts)}
    # "Security is a priority." alone is not a name; "Acme is a vendor. We pay Acme." is
    again = {k for k, word in single.items()
             if sum(len(re.findall(rf"(?<![\w-]){re.escape(word)}(?![\w-])", t)) for t in corpus) >= 2}
    return [(k, first[k].ref, first[k]) for k in sorted(definite | repeated | again)]
