"""Findings: what the judge's validated verdicts, the coverage gate and run B flag, ranked for a person to decide.

Also the fingerprint of what was evaluated, the committed summary (kb/eval/summary.yaml) and the `kb eval report`
Markdown. A finding only proposes: a person resolves it (Task 9's `kb eval decide`).
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from kb.eval.align import Alignment, render_agreement
from kb.eval.judge import YES_NO
from kb.ingest.report import issue_lines, section
from kb.issues import Issue
from kb.records.lifecycle import live_ids
from kb.records.repo import KbRepo
from kb.safewrite import check_target, write_atomic
from kb.textfmt import inline_code
from kb.yamlio import dump_yaml

SUMMARY = "kb/eval/summary.yaml"
SUPPORTERS = 3  # the kb coverage gate (or kb's entity detector), run B, the judge
_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Finding:
    id: str  # the item's public id (F-…): what a person and every later command name it by
    canonical: str  # the item's canonical id (R:, S:, B:, …), internal
    kind: str  # omission, unfaithful, split, merge, unknown
    ref: str
    text: str
    severity: str  # high, medium, low
    supporters: int  # how many of {kb coverage gate, run B, judge} flagged it
    related: tuple[str, ...]
    note: str
    kind_hint: str | None = None  # an omission's: the judge's requirement/decision/invariant/exclusion, or entity/relation
    # an entity omission's names (one tuple), a relation omission's source and target names (two): what a TERM added
    # for it must be named by (kb eval decide)
    ends: tuple[tuple[str, ...], ...] = ()


def _overlap(a: list, b: list) -> bool:
    """Whether some span of `a` and some span of `b` ([src, start, end]) overlap."""
    return any(x[0] == y[0] and max(x[1], y[1]) < min(x[2], y[2]) for x in a for y in b)


def _finding(item: dict, v: dict, b_spans: list[list], s_spans: list[list], b_names: set[str]) -> Finding | None:
    kind, verdict, note = item["kind"], v.get("verdict"), v.get("note", "")
    related = tuple(v.get("related", ()))

    def make(fkind: str, severity: str, supporters: int, kind_hint: str | None = None, text_note: str = note) -> Finding:
        ends: tuple[tuple[str, ...], ...] = ()
        if kind == "entity_missing":
            ends = (tuple(item.get("names", ())),)
        elif kind == "relation_missing":
            ends = tuple(tuple(e) for e in item.get("ends", ()))
        return Finding(item["public"], item["id"], fkind, item["ref"], item["text"], severity, supporters, related,
                       text_note, kind_hint, ends)

    if kind in YES_NO:
        noes = [q for q in YES_NO[kind] if v.get(q) == "no"]
        if not noes:
            return None
        return make("unfaithful", "high", 1, text_note="no: " + ", ".join(noes) + (f"; {note}" if note else ""))
    flagged = verdict == "missing"  # the judge flags it; `unknown` is kept, but unflagged
    if kind in ("uncovered", "only_b"):
        if verdict not in ("missing", "unknown"):
            return None
        other = b_spans if kind == "uncovered" else s_spans  # run B also extracted it / the coverage gate also flagged it
        supporters = 1 + flagged + any(_overlap(item["spans"], o) for o in other)
        return make("omission", v.get("severity") or "medium", supporters, v.get("kind_hint"))
    if kind == "split_merge":
        fkind = {"should_split": "split", "should_merge": "merge", "unknown": "unknown"}.get(verdict)
        return make(fkind, "low", 1 + (verdict != "unknown")) if fkind else None
    if verdict not in ("missing", "unknown"):
        return None
    if kind == "entity_missing":
        supporters = bool(set(item["names"]) & b_names) + bool(item.get("detected")) + flagged
        return make("omission", "low", supporters, "entity")
    return make("omission", "low", 1 + flagged, "relation")


def build_findings(items: list[dict], verdicts: list[dict], alignment: Alignment) -> list[Finding]:
    """The findings of the validated `verdicts` (by canonical id), ranked: high severity first, then more supporters,
    then canonical id. Each is named by its item's public id.

    An S or B item judged missing or unknown is an omission; an S item that run B also extracted (or a B item the
    coverage gate also flagged) has every supporter. A B omission on the sentence of an S item judged missing is the
    same gap when the B item is judged missing too: it is folded into the S finding, which keeps the higher of the
    two severities (and B's kind_hint when it has none). When either is judged unknown, the B omission stands. An R, T or L item with any "no" is
    unfaithful. A split_merge item not judged ok is a split, merge or unknown. An E or N item judged missing or
    unknown is an omission with kind_hint entity or relation."""
    by_id = {v["id"]: v for v in verdicts}
    b_spans = [[list(s) for s in b.spans] for b in alignment.only_b]
    s_spans = [i["spans"] for i in items if i["kind"] == "uncovered"]
    b_names = {n for e in alignment.only_b_entities for n in e.names}
    found = {f.canonical: f for i in items if i["id"] in by_id and (f := _finding(i, by_id[i["id"]], b_spans, s_spans, b_names))}
    # a person is asked once about one gap: a B omission folds into the first S item judged missing it overlaps
    missing_s = [i for i in items if i["kind"] == "uncovered" and i["id"] in found and by_id[i["id"]].get("verdict") == "missing"]
    for item in items:
        b = found.get(item["id"])
        # only a B item judged missing folds: an unknown one stands, and its default severity never raises S
        if item["kind"] != "only_b" or b is None or by_id[item["id"]].get("verdict") != "missing":
            continue
        s_item = next((s for s in missing_s if _overlap(item["spans"], s["spans"])), None)
        if s_item is not None:
            s = found[s_item["id"]]
            found[s.canonical] = replace(s, severity=min(s.severity, b.severity, key=_RANK.__getitem__),
                                         kind_hint=s.kind_hint or b.kind_hint)
            del found[b.canonical]
    return sorted(found.values(), key=lambda f: (_RANK[f.severity], -f.supporters, f.canonical))


# a dismissal: an item kb (or run B) flagged that the judge said needs no record. Only a person settles it (kb todo's
# `dismissed` ask): the judge alone never dismisses a rule-like sentence kb flagged
DISMISSING = {"uncovered": ("covered", "not_a_rule"), "only_b": ("covered", "not_a_rule"),
              "entity_missing": ("covered", "not_an_entity"), "relation_missing": ("covered", "not_an_entity")}
_DISMISSED_KIND = {"uncovered": "omission", "only_b": "omission", "entity_missing": "entity",
                   "relation_missing": "relation"}


@dataclass(frozen=True)
class Dismissed:
    id: str  # the item's public id: what `kb eval decide <id> --as reopen` names
    canonical: str
    kind: str  # omission, entity or relation: the finding it becomes when a person reopens it
    ref: str
    text: str
    verdict: str  # covered, not_a_rule or not_an_entity
    related: tuple[str, ...]
    note: str
    ends: tuple[tuple[str, ...], ...] = ()  # as Finding.ends


def build_dismissed(items: list[dict], verdicts: list[dict]) -> list[Dismissed]:
    """Every S, B, E or N item the validated `verdicts` dismiss (covered, not a rule, not an entity), in item order."""
    by_id = {v["id"]: v for v in verdicts}
    out = []
    for item in items:
        v = by_id.get(item["id"])
        if v is None or v.get("verdict") not in DISMISSING.get(item["kind"], ()):
            continue
        ends: tuple[tuple[str, ...], ...] = ()
        if item["kind"] == "entity_missing":
            ends = (tuple(item.get("names", ())),)
        elif item["kind"] == "relation_missing":
            ends = tuple(tuple(e) for e in item.get("ends", ()))
        out.append(Dismissed(item["public"], item["id"], _DISMISSED_KIND[item["kind"]], item["ref"], item["text"],
                             v["verdict"], tuple(v.get("related", ())), v.get("note", ""), ends))
    return out


def dismissed_dict(d: Dismissed) -> dict:
    return asdict(d) | {"related": list(d.related), "ends": [list(e) for e in d.ends]}


def fingerprint(repo: KbRepo, skip: Iterable[str] = ()) -> str:
    """What was evaluated: sha256 over the live records (id and raw bytes, by id) and every stored source's segment
    hashes, first 16 hex characters. kb/eval/ holds no record, so the summary and decisions never change it. `skip`
    leaves those records out (kb.eval.decisions.is_fresh: records a person's decision settled)."""
    # triage is left out on purpose: which S items exist depends only on quotes (records) and decisions, not on triage
    h = hashlib.sha256()
    for rid in sorted(live_ids(repo) - set(skip)):
        data = repo.raw.get(repo.paths[rid], b"")
        h.update(f"record {rid} {len(data)}\n".encode())
        h.update(data)
    for src in sorted(repo.sources):
        for seg in repo.segments(src):
            h.update(f"segment {src}#{seg.id} {seg.hash}\n".encode())
    return h.hexdigest()[:16]


def finding_dict(f: Finding) -> dict:
    return asdict(f) | {"related": list(f.related), "ends": [list(e) for e in f.ends]}


def summary(commit: str, fp: str, salt: str, coverage: dict, agreement: dict, findings: list[Finding], judged_at: str,
            dismissed: Sequence[Dismissed] = ()) -> dict:
    """The committed summary, in its key order. `salt` (hex) keys the cycle's public ids; later commands resolve an id
    through `open_findings` and `dismissed` (id and canonical), never by recomputing it."""
    return {"commit": commit, "fingerprint": fp, "salt": salt, "coverage": coverage, "agreement": agreement,
            "open_findings": [finding_dict(f) for f in findings], "dismissed": [dismissed_dict(d) for d in dismissed],
            "judged_at": judged_at}


def write_summary(root: Path, data: dict) -> None:
    check_target(Path(root), SUMMARY, "file")  # never through a symlink
    write_atomic(Path(root), SUMMARY, dump_yaml(data))


# --- the report --------------------------------------------------------------------------------------------------

_SECTIONS = (("omission", "Omissions"), ("unfaithful", "Unfaithful records"), ("split", "Should be split"),
             ("merge", "Should be merged"), ("unknown", "Granularity unknown"))


def _pct(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else "n/a"


def _finding_line(f: Finding) -> str:
    parts = [inline_code(f.id), f.severity, f"{f.supporters} of {SUPPORTERS} supporters", inline_code(f.ref)]
    if f.kind_hint:
        parts.append(f"as {f.kind_hint}")
    parts.append(inline_code(f.text))
    if f.related:
        parts.append("related " + ", ".join(inline_code(r) for r in f.related))
    if f.note:
        parts.append("note " + inline_code(f.note))
    return "- " + " · ".join(parts)


def _dismissed_line(d: Dismissed) -> str:
    parts = [inline_code(d.id), d.verdict.replace("_", " "), inline_code(d.ref), inline_code(d.text)]
    if d.related:
        parts.append("related " + ", ".join(inline_code(r) for r in d.related))
    if d.note:
        parts.append("note " + inline_code(d.note))
    return "- " + " · ".join(parts)


def render_eval_report(coverage: dict, agreement: dict, findings: list[Finding], issues: list[Issue],
                       run_b_issues: Sequence[Issue] = (), dismissed: Sequence[Dismissed] = ()) -> str:
    """Markdown; every piece of untrusted text (item ids, document text, notes, messages) is one-line inline code."""
    out = ["# Extraction evaluation", "", "## Coverage", ""]
    rules = f"{coverage['covered']} of {coverage['normative']} ({_pct(coverage['covered'], coverage['normative'])})"
    quoted = (f"{coverage['quoted_chars']} of {coverage['text_chars']} characters "
              f"({_pct(coverage['quoted_chars'], coverage['text_chars'])})")
    out += [f"- rule-like sentences quoted by a record: {rules}",
            f"- text quoted at all: {quoted}; quoting whole segments raises coverage and shows here", ""]
    out += ["## Agreement with a blind second extraction", "", render_agreement(agreement).rstrip("\n"), ""]
    out += [f"## Findings ({len(findings)})", ""]
    for kind, title in _SECTIONS:
        out += section(title, [_finding_line(f) for f in findings if f.kind == kind], "###")
    out += section("Dismissed by the judge, for a person to agree with", [_dismissed_line(d) for d in dismissed])
    out += section("Rejected verdicts", issue_lines([i for i in issues if i.code == "eval.verdict_invalid"]))
    out += section("Missing verdicts", issue_lines([i for i in issues if i.code == "eval.verdict_missing"]))
    if run_b_issues:
        out += section("Run B items set aside", issue_lines(list(run_b_issues)))
    return "\n".join(out)
