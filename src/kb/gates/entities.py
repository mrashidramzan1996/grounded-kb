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


def names(term: Term) -> list[str]:
    """The term's normalised name and aliases, without empties and duplicates."""
    return [n for n in dict.fromkeys(normalise_name(x) for x in (term.name, *term.aliases)) if n]


def _contains(haystack: str, needle: str) -> bool:
    """`needle` in `haystack` (both normalised), not starting or ending inside a word or number (as find_quote)."""
    at = haystack.find(needle)
    while at >= 0:
        end = at + len(needle)
        starts_ok = not (needle[0].isalnum() and at > 0 and haystack[at - 1].isalnum())
        ends_ok = not (needle[-1].isalnum() and end < len(haystack) and haystack[end].isalnum())
        if starts_ok and ends_ok:
            return True
        at = haystack.find(needle, at + 1)
    return False


def mentions(text: str, term: Term) -> bool:
    """Whether `text` names `term` by its name or an alias."""
    folded = normalise_name(text)
    return any(_contains(folded, n) for n in names(term))


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
    if mentions(rel.quote.text, term) and mentions(rel.quote.text, target):
        return []
    return [Issue("relation.entities_not_in_quote", f"{what}: the quote does not name both {tid} and {rel.target}: "
                  f"{inline_code(rel.quote.text)}", "warning", path, tid)]


def entity_gate(ctx: GateContext) -> list[Issue]:
    """Live terms: quoted evidence (quote.*), the name in a quote, relation quotes. Name and relation-mention problems are
    warnings here, so a 0.3 repo still passes; the ingest and redraft reports make them errors for drafted terms."""
    repo, issues = ctx.repo, []
    live = live_ids(repo)
    for tid, term in sorted(repo.records.items()):
        if not isinstance(term, Term) or tid not in live:
            continue
        path = repo.paths[tid]
        ev = term.evidence
        if ev is None:
            if term.kind is not None:
                issues.append(Issue("entity.unquoted", f"{tid}: a term with a kind quotes no source; add evidence.quotes with a "
                                    "passage that names it", "warning", path, tid))
        else:
            found, _ = quote_results(repo, ev)
            issues.extend(evidence_issues(repo, tid, ev, found, True, path))
            if not any(mentions(q.text, term) for q in ev.quotes):
                issues.append(Issue("entity.name_not_in_quote", f"{tid}: neither the name nor an alias appears in its quotes; "
                                    "quote a passage that names it", "warning", path, tid))
        for rel in term.relations:
            issues.extend(_relation_issues(repo, tid, term, rel, path))
    return issues
