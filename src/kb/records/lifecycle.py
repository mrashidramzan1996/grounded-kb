"""Derived lifecycle: superseded-ness is computed from live records, never stored."""

from kb.records.models import Decision, Invariant
from kb.records.repo import KbRepo


def supersede_edges(repo: KbRepo) -> dict[str, list[str]]:
    """target -> sorted superseder ids, from live records naming an existing record of their own type."""
    edges: dict[str, set[str]] = {}
    for rid, rec in repo.records.items():
        if not isinstance(rec, (Decision, Invariant)) or rec.status != "live" or not rec.supersedes:
            continue
        target = repo.records.get(rec.supersedes)
        if target is not None and type(target) is type(rec):
            edges.setdefault(rec.supersedes, set()).add(rid)
    return {t: sorted(s) for t, s in edges.items()}


def superseded_by(repo: KbRepo) -> dict[str, str]:
    """target -> superseder; with several, the lexicographically smallest."""
    return {t: s[0] for t, s in supersede_edges(repo).items()}


def live_ids(repo: KbRepo) -> set[str]:
    gone = superseded_by(repo)
    return {rid for rid, rec in repo.records.items() if rec.status == "live" and rid not in gone}

