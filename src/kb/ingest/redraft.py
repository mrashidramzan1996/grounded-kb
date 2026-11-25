"""What `kb redraft` offers: records citing changed text, flagged, or whose quote vanished, and new text no record covers."""

from importlib.resources import files
from pathlib import Path
from string import Template

from kb.gates.quotes import quote_results
from kb.ingest.brief import BRIEF_VERSION, INJECTION_FLAG, _fence, _segments, next_ids
from kb.issues import Issue
from kb.records.lifecycle import live_ids
from kb.records.models import QUOTED_TYPES, Decision, Exclusion, Invariant, Requirement
from kb.records.repo import KbRepo
from kb.sources.guard import has_injection
from kb.textfmt import inline_code

REDRAFT_BRIEF = ".kb/cache/redraft/brief.md"
REDRAFT_BASE = ".kb/cache/redraft/base"  # HEAD when the brief was written
REDRAFT_IDS = ".kb/cache/redraft/ids"  # selected ids, one per line, sorted
REDRAFT_NEW = ".kb/cache/redraft/new"  # segments new records may cite: those offered to draft from plus the changed cited ones
REDRAFT_FILES = (REDRAFT_BRIEF, REDRAFT_BASE, REDRAFT_IDS, REDRAFT_NEW)

_Cited = Requirement | Exclusion | Decision | Invariant


class RedraftError(ValueError):
    """An id that is unknown, not live, or not a requirement, exclusion, decision or invariant."""


def segment_hashes(repo: KbRepo) -> dict[str, str]:
    """'SRC-x#seg-NN' -> hash, for every stored source."""
    return {f"{src}#{seg.id}": seg.hash for src in repo.sources for seg in repo.segments(src)}


def changed_segments(before: KbRepo, after: KbRepo) -> set[str]:
    """Refs of `before` whose hash differs at `after`, or that are gone there."""
    now = segment_hashes(after)
    return {ref for ref, h in segment_hashes(before).items() if now.get(ref) != h}


def ref_key(ref: str) -> tuple[str, int, str]:
    """Sort key for "SRC-x#seg-NN" refs: by source, then segment number."""
    src, _, seg = ref.partition("#")
    num = seg.rsplit("-", 1)[-1]
    return src, int(num) if num.isdigit() else -1, seg


def untriaged(repo: KbRepo) -> list[str]:
    """Refs of stored segments with no triage entry (as the triage gate sees them), in segment order."""
    out = []
    for src in repo.sources:
        tri = repo.triage(src)
        done = tri.segments if tri else {}
        out.extend(f"{src}#{seg.id}" for seg in repo.segments(src) if seg.id not in done)
    return sorted(out, key=ref_key)


def citers(repo: KbRepo) -> dict[str, list[str]]:
    """'SRC-x#seg-NN' -> the live REQ/EXCL/ADR/INV ids quoting it, sorted."""
    out: dict[str, list[str]] = {}
    for rid, rec in _live_cited(repo):
        for ref in sorted({q.source for q in rec.evidence.quotes} if rec.evidence else set()):
            out.setdefault(ref, []).append(rid)
    return out


def new_text(repo: KbRepo, base: KbRepo) -> list[str]:
    """The untriaged segments of `repo` that no live record of `base` cites: text with no records yet, which
    --new offers. An untriaged segment a record already cites is not new text (see untriaged_cited)."""
    cited = citers(base)
    return [ref for ref in untriaged(repo) if ref not in cited]


def untriaged_cited(repo: KbRepo, base: KbRepo, refs: set[str] | None = None) -> list[Issue]:
    """segment.untriaged_cited for each untriaged segment of `repo` (of `refs`, when given) that a live record of
    `base` cites: its triage entry was never written, and only a person can add it."""
    cited, issues = citers(base), []
    for ref in untriaged(repo):
        if ref in cited and (refs is None or ref in refs):
            src = ref.partition("#")[0]
            issues.append(Issue("segment.untriaged_cited",
                                f"{ref} is cited by {', '.join(cited[ref])} but has no triage entry; add one by hand",
                                "warning", path=f"kb/sources/{src}/triage.yaml", record=src))
    return issues


def _live_cited(repo: KbRepo) -> list[tuple[str, _Cited]]:
    live = live_ids(repo)
    return [(rid, rec) for rid, rec in sorted(repo.records.items()) if rid in live and isinstance(rec, QUOTED_TYPES)]


def citing(repo: KbRepo, refs: set[str]) -> list[str]:
    """Live REQ/EXCL/ADR/INV ids with a quote citing any of `refs`."""
    return [rid for rid, rec in _live_cited(repo) if rec.evidence and any(q.source in refs for q in rec.evidence.quotes)]


def flagged(repo: KbRepo) -> list[str]:
    """Live requirement ids with a non-empty `flags`."""
    return [rid for rid, rec in _live_cited(repo) if isinstance(rec, Requirement) and rec.flags]


def unfound(repo: KbRepo) -> list[str]:
    """Live REQ/EXCL/ADR/INV ids with a quote not found in its segment (or whose segment is missing); under mode any, with none found."""
    out = []
    for rid, rec in _live_cited(repo):
        if rec.evidence is None:
            continue
        found, missing = quote_results(repo, rec.evidence)
        if missing and (rec.evidence.mode == "all" or not found):
            out.append(rid)
    return out


def check_named(repo: KbRepo, rid: str) -> None:
    """RedraftError unless `rid` is a live requirement, exclusion, decision or invariant of `repo`."""
    rec = repo.records.get(rid)
    if rec is None:
        raise RedraftError(f"{rid} is unknown")
    if not isinstance(rec, QUOTED_TYPES):
        raise RedraftError(f"{rid} is a {type(rec).__name__}; only requirements, exclusions, decisions and invariants can be redrafted")
    if rid not in live_ids(repo):
        raise RedraftError(f"{rid} is not live")


def select(repo: KbRepo, ids: list[str], *, flagged_: bool, changed: bool, before: KbRepo | None) -> dict[str, list[str]]:
    """id -> sorted reasons, from: "named", "flagged <flag>" per flag, "cites changed text <ref>", "quote not found <ref>".
    changed=True uses changed_segments(before, repo) when before is given plus unfound(repo).
    The CLI passes before = KbRepo.at_ref(root, --base or "HEAD"): run it before committing the sync, or pass
    --base <commit before the sync> afterwards."""
    reasons: dict[str, set[str]] = {}

    def add(rid: str, why: str) -> None:
        reasons.setdefault(rid, set()).add(why)

    for rid in ids:
        check_named(repo, rid)
        add(rid, "named")
    cited = dict(_live_cited(repo))
    if flagged_:
        for rid in flagged(repo):
            for flag in cited[rid].flags:
                add(rid, f"flagged {flag}")
    if changed:
        if before is not None:
            for ref in changed_segments(before, repo):
                for rid in citing(repo, {ref}):
                    add(rid, f"cites changed text {ref}")
        for rid in unfound(repo):
            for q in quote_results(repo, cited[rid].evidence)[1]:
                add(rid, f"quote not found {q.source}")
    return {rid: sorted(reasons[rid]) for rid in sorted(reasons)}


def present(repo: KbRepo, ref: str) -> bool:
    """Whether the segment "SRC-x#seg-NN" is stored in `repo`."""
    src, _, seg_id = ref.partition("#")
    return src in repo.sources and any(s.id == seg_id for s in repo.segments(src))


def changed_text(before: KbRepo, repo: KbRepo) -> tuple[set[str], set[str]]:
    """(cited, uncited): the changed refs still stored in `repo`, by whether a live record there cites them. A --changed
    brief shows the cited ones with the records to redraft, and offers the uncited ones as text to draft from."""
    stored = {ref for ref in changed_segments(before, repo) if present(repo, ref)}
    cited = {ref for ref in stored if citing(repo, {ref})}
    return cited, stored - cited


def _record_section(repo: KbRepo, rid: str, why: list[str]) -> str:
    path = repo.paths[rid]
    content = repo.raw[path].decode("utf-8", errors="replace")
    lang = "markdown" if path.endswith(".md") else "yaml"
    fence = _fence(content)
    # the file holds copied quotes, so it is source text too: flagged like a segment
    flag = [INJECTION_FLAG] if has_injection(content) else []
    lines = [f"## {rid}", "", "Reasons: " + "; ".join(why), "", f"Current file {inline_code(path)}:", "", *flag, f"{fence}{lang}\n{content}{fence}", ""]
    quotes = repo.records[rid].evidence.quotes if repo.records[rid].evidence else []
    refs = sorted({q.source for q in quotes})
    if not refs:
        lines.append("Cited segments: no quotes")
        return "\n".join(lines)
    lines.append("Cited segments:")
    stored = [r for r in refs if present(repo, r)]
    lines.extend(f"\ncited segment {r} is missing" for r in refs if r not in stored)
    if stored:
        lines.append("\n" + _segments(repo, sorted({r.partition("#")[0] for r in stored}), only=set(stored)))
    return "\n".join(lines)


def _existing_requirements(repo: KbRepo) -> str:
    """Each live requirement as `- <id>: <statement>`, the statement as inline code so it cannot add markup; an
    instruction-like statement gets the INJECTION_FLAG line above it, like a segment."""
    lines = []
    for rid, rec in _live_cited(repo):
        if isinstance(rec, Requirement):
            if has_injection(rec.statement):
                lines.append(INJECTION_FLAG)
            lines.append(f"- {rid}: {inline_code(rec.statement)}")
    return "\n".join(lines) or "- none"


def _template(name: str) -> Template:
    return Template(files("kb.ingest").joinpath("templates", name).read_text(encoding="utf-8"))


def build_redraft_brief(root: Path, repo: KbRepo, selection: dict[str, list[str]], base: str, new: list[str] | None = None) -> str:
    """The brief for the selected records (part 1) and the segments `new` to draft from (part 2); deterministic
    for the same repo state, selection and segments."""
    parts = []
    if selection:
        records = "\n\n".join(_record_section(repo, rid, selection[rid]) for rid in sorted(selection))
        parts.append(_template("redraft_records.md").substitute(version=BRIEF_VERSION, records=records))
    if new:
        parts.append(_template("redraft_new.md").substitute(
            version=BRIEF_VERSION,
            requirements=_existing_requirements(repo),
            segments=_segments(repo, sorted({r.partition("#")[0] for r in new}), only=set(new)),
        ))
    return _template("redraft.md").substitute(
        version=BRIEF_VERSION,
        base=base,
        next_ids="\n".join(f"- {label}: {nid}" for label, nid in next_ids(repo)),
        parts="\n".join(parts),
    )
