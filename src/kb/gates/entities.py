"""Grounded entities (0.4.0): a glossary term with `kind`, quoted `evidence` and quoted `relations`.

A term's quotes must name it, and a relation's quote must be found in its segment and name both terms. Names and
aliases match case-insensitively on word boundaries after quotes.match.fold; plurals match only through an alias.
"""

from kb.gates.base import GateContext
from kb.gates.quotes import MIN_QUOTE_CHARS, evidence_issues, quote_results, too_short
from kb.issues import Issue
from kb.quotes.match import fold
from kb.records.lifecycle import live_ids
from kb.records.models import Evidence, Relation, Term
from kb.records.repo import KbRepo
from kb.textfmt import inline_code


def normalise_name(text: str) -> str:
    """A name or alias as it is compared: folded (typography, whitespace runs), stripped and lower-cased."""
    return fold(text)[0].strip().lower()


# never a name on their own: they occur in nearly every quote
STOP_WORDS = frozenset({"a", "an", "the", "of", "and", "or", "is", "to", "in", "on", "for", "by", "at", "as", "be", "it"})


def _usable(name: str) -> bool:
    """A normalised name or alias that can name something: 2 or more letters or digits, and not a stop word."""
    return sum(c.isalnum() for c in name) >= 2 and name not in STOP_WORDS


def names(term: Term) -> list[str]:
    """The term's normalised name and aliases, without duplicates; one that cannot name anything (see _usable) is left
    out, and reported by the gate as entity.alias_invalid."""
    return [n for n in dict.fromkeys(normalise_name(x) for x in (term.name, *term.aliases)) if _usable(n)]


def unusable_names(term: Term) -> list[str]:
    """The name and aliases (as written) that names() leaves out."""
    return [x for x in dict.fromkeys((term.name, *term.aliases)) if not _usable(normalise_name(x))]


def _spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Every [start, end) of `needle` in `haystack` (both normalised) not starting or ending inside a word or number
    (as find_quote)."""
    out = []
    at = haystack.find(needle)
    while at >= 0:
        end = at + len(needle)
        starts_ok = not (needle[0].isalnum() and at > 0 and haystack[at - 1].isalnum())
        ends_ok = not (needle[-1].isalnum() and end < len(haystack) and haystack[end].isalnum())
        if starts_ok and ends_ok:
            out.append((at, end))
        at = haystack.find(needle, at + 1)
    return out


def mention_spans(text: str, term: Term) -> list[tuple[int, int]]:
    """Where `text` (normalised) names `term` by its name or an alias."""
    folded = normalise_name(text)
    return [span for n in names(term) for span in _spans(folded, n)]


def mentions(text: str, term: Term) -> bool:
    """Whether `text` names `term` by its name or an alias."""
    return bool(mention_spans(text, term))


def names_both(text: str, a: Term, b: Term) -> bool:
    """Whether `text` names `a` and `b` at two places that do not overlap: one occurrence never names both ends."""
    return any(x[1] <= y[0] or y[1] <= x[0] for x in mention_spans(text, a) for y in mention_spans(text, b))


def _segment_exists(repo: KbRepo, ref: str) -> bool:
    src, _, seg_id = ref.partition("#")
    return src in repo.sources and any(s.id == seg_id for s in repo.segments(src))


def _relation_issues(repo: KbRepo, tid: str, term: Term, rel: Relation, path: str) -> list[Issue]:
    what = f"{tid}: relation {rel.type} {rel.target}"
    if not _segment_exists(repo, rel.quote.source):
        return []  # id_gate reports the missing source or segment
    found, _ = quote_results(repo, Evidence(quotes=[rel.quote]))
    if not found:
        why = f"quote shorter than {MIN_QUOTE_CHARS} characters" if too_short(rel.quote.text) else f"quote not found in {rel.quote.source}"
        return [Issue("relation.quote_not_found", f"{what}: {why}: {inline_code(rel.quote.text)}", path=path, record=tid)]
    target = repo.records.get(rel.target)
    if not isinstance(target, Term):
        return []  # id_gate reports the missing target
    if names_both(rel.quote.text, term, target):
        return []
    return [Issue("relation.entities_not_in_quote", f"{what}: the quote does not name both {tid} and {rel.target}: "
                  f"{inline_code(rel.quote.text)}", "warning", path, tid)]


def _name_issues(repo: KbRepo, live: set[str]) -> list[Issue]:
    """entity.alias_invalid for a name or alias that cannot name anything; entity.alias_conflict for one that another
    live term also has."""
    issues: list[Issue] = []
    owners: dict[str, list[str]] = {}
    terms = [(tid, t) for tid, t in sorted(repo.records.items()) if isinstance(t, Term) and tid in live]
    for tid, term in terms:
        for n in names(term):
            owners.setdefault(n, []).append(tid)
    for tid, term in terms:
        path = repo.paths[tid]
        for x in unusable_names(term):
            issues.append(Issue("entity.alias_invalid", f"{tid}: {inline_code(normalise_name(x) or x)} is ignored as a name or alias: "
                                "it needs 2 letters or digits and must not be a stop word", "warning", path, tid))
        for n in names(term):
            for other in owners[n]:
                if other != tid:
                    issues.append(Issue("entity.alias_conflict", f"{tid}: {inline_code(n)} is also a name or alias of {other}",
                                        "warning", path, tid))
    return issues


def entity_gate(ctx: GateContext) -> list[Issue]:
    """Live terms: usable, unshared names; quoted evidence (quote.*) whose found quotes name the term; relation quotes.
    Name and relation-mention problems are warnings here, so a 0.3 repo still passes; the ingest and redraft reports make
    them errors for drafted terms."""
    repo = ctx.repo
    live = live_ids(repo)
    issues = _name_issues(repo, live)
    for tid, term in sorted(repo.records.items()):
        if not isinstance(term, Term) or tid not in live:
            continue
        path = repo.paths[tid]
        ev = term.evidence
        if ev is None:
            # a 0.3 term (no kind, written by a person) stays quiet; a drafted or typed one must quote its document
            if term.kind is not None or term.drafted_by:
                issues.append(Issue("entity.unquoted", f"{tid}: this term quotes no source; add evidence.quotes with a "
                                    "passage that names it", "warning", path, tid))
        else:
            found, _ = quote_results(repo, ev)
            issues.extend(evidence_issues(repo, tid, ev, found, True, path))
            # only a quote that exists shows the document names the term
            if not any(mentions(q.text, term) for q in found):
                issues.append(Issue("entity.name_not_in_quote", f"{tid}: neither the name nor an alias appears in its found quotes; "
                                    "quote a passage that names it", "warning", path, tid))
        for rel in term.relations:
            issues.extend(_relation_issues(repo, tid, term, rel, path))
    return issues
