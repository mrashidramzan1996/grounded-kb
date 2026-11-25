from kb.gates.base import GateContext
from kb.gates.quotes import located_quotes
from kb.issues import Issue
from kb.records.ids import valid_segment_id
from kb.records.lifecycle import live_ids
from kb.records.models import (
    NONE_CODES,
    Component,
    Feature,
    Requirement,
    Term,
    none_code,
)
from kb.records.repo import KbRepo
from kb.textfmt import inline_code

# Task 6: a term is grounded through the requirements that name it in `entities`; until then TERM and COMP ids in a
# triage list neither satisfy nor fail triage.not_quoted
_SKIPPED = (Term, Component)


def quotes_ref(repo: KbRepo, rid: str, ref: str, live: set[str] | None = None) -> bool:
    """Whether live record `rid` has a found quote (located_quotes) inside segment `ref` ("SRC-x#seg-NN"); for a
    feature, whether one of the live requirements it or its claims link does."""
    live = live_ids(repo) if live is None else live
    rec = repo.records.get(rid)
    if rec is None or rid not in live:
        return False
    if isinstance(rec, Feature):
        linked = dict.fromkeys([*rec.requirements, *(c.requirement for c in rec.claims if c.requirement)])
        return any(isinstance(repo.records.get(r), Requirement) and quotes_ref(repo, r, ref, live) for r in linked)
    evidence = getattr(rec, "evidence", None)
    src, _, seg_id = ref.partition("#")
    seg = next((s for s in repo.segments(src) if s.id == seg_id), None)
    if evidence is None or seg is None:
        return False
    return any(s == src and seg.start <= a and b <= seg.end for _, s, a, b in located_quotes(repo, evidence))


def unquoted_entries(repo: KbRepo) -> list[tuple[str, str, list[str]]]:
    """(source id, segment key, named ids) for each triage list none of whose checkable records quotes its segment.
    Missing ids are id_gate's (ref.missing); TERM and COMP ids are skipped (_SKIPPED)."""
    live, out = live_ids(repo), []
    for src_id in sorted(repo.sources):
        tri = repo.triage(src_id)
        for seg_id, value in sorted(tri.segments.items()) if tri else ():
            if isinstance(value, str):
                continue
            known = [rid for rid in value if rid in repo.records and not isinstance(repo.records[rid], _SKIPPED)]
            if known and not any(quotes_ref(repo, rid, f"{src_id}#{seg_id}", live) for rid in known):
                out.append((src_id, seg_id, known))
    return out


def _ref(src_id: str, seg_id: str) -> str:
    return f"{src_id}#{seg_id}" if valid_segment_id(seg_id) else inline_code(f"{src_id}#{seg_id}")


def not_quoted_issue(src_id: str, seg_id: str, known: list[str]) -> Issue:
    """A warning here, so a 0.3 repo still passes `kb check`; the ingest and redraft reports make a new one an error."""
    return Issue("triage.not_quoted", f"{_ref(src_id, seg_id)}: triage names {', '.join(inline_code(k) for k in known)} "
                 "but none of them quotes this segment", "warning", f"kb/sources/{src_id}/triage.yaml", src_id)


def triage_gate(ctx: GateContext) -> list[Issue]:
    """Every segment has a triage entry; a list names a record that quotes the segment; a `none:` reason has a code."""
    repo, issues = ctx.repo, []
    for src_id in sorted(repo.sources):
        path = f"kb/sources/{src_id}/triage.yaml"
        tri = repo.triage(src_id)
        triaged = tri.segments if tri else {}
        for seg in repo.segments(src_id):
            if seg.id not in triaged:
                issues.append(Issue(
                    "segment.untriaged",
                    f"`{src_id}#{seg.id}` {inline_code(seg.heading)} has no triage entry",
                    "warning",
                    path=path,
                    record=src_id,
                ))
        for seg_id, value in sorted(triaged.items()):
            if not isinstance(value, str):
                continue
            code = none_code(value)
            if code is None:
                issues.append(Issue(
                    "triage.none_uncoded",
                    f"{_ref(src_id, seg_id)}: {inline_code(value)} gives no reason code; write `none: <code>[: reason]` "
                    f"with one of {', '.join(NONE_CODES)}",
                    "warning", path, src_id,
                ))
            elif code == "out-of-scope":
                issues.append(Issue("triage.use_exclusion", f"{_ref(src_id, seg_id)}: triaged out-of-scope; record it as an "
                                    "EXCL record instead (`kb/exclusions/EXCL-NNN.yaml`)", "warning", path, src_id))
    issues.extend(not_quoted_issue(*entry) for entry in unquoted_entries(repo))
    return issues
