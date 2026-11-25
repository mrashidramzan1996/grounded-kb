"""A person's decisions on the findings of an evaluation (kb/eval/decisions.yaml), and the summary they resolve against.

`kb eval decide` validates a decision against the knowledge base and appends it atomically. A finding is named by its
public id (F-…, from kb/eval/summary.yaml's open_findings, never recomputed) or by the pseudo-finding `categories`.
A decision that a sentence is added, covered or not a rule keys on the sentence (sentence_key), so it keeps suppressing
that sentence after a later `kb eval judge` cycle gives its finding another public id.
"""

import datetime
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kb.clarify import MAX_BY, ClarifyError, _plain
from kb.eval import EvalError
from kb.eval.findings import SUMMARY, fingerprint
from kb.gates.coverage import COVERING_TYPES
from kb.gates.entities import mentions, names, normalise_name
from kb.gates.quotes import located_quotes
from kb.gates.sources import bad_segment_ids
from kb.gitfiles import GitError, rev_parse
from kb.graph import entity_key
from kb.issues import Issue
from kb.quotes.match import find_quote
from kb.records.ids import ADR_ID, COMP_ID, EXCL_ID, FEAT_ID, INV_ID, REQ_ID, TERM_ID
from kb.records.lifecycle import live_ids
from kb.records.models import Evidence, Term
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular
from kb.safewrite import check_target, write_atomic
from kb.sources.sentences import sentence_key, sentences
from kb.textfmt import inline_code
from kb.yamlio import YamlError, dump_yaml, load_yaml_strict

DECISIONS = "kb/eval/decisions.yaml"
CATEGORIES = "categories"  # the pseudo-finding of the categories ask: no document states some kinds of requirement
PUBLIC_ID = re.compile(r"F-[0-9a-f]{10}")
DECISION_VALUES = ("added", "covered", "not_a_rule", "product_decision", "correct", "fixed", "ok")
SUPPRESSING = frozenset({"added", "covered", "not_a_rule"})  # decisions that settle a sentence: it stops warning
# what changes a record: a decision naming such a record also settles that change (the summary stays current)
_CHANGING = frozenset({"added", "fixed"})
MAX_REASON = 200
MAX_FILE_BYTES = 8 * 1024 * 1024
_HANDLE = re.compile(r"@[\w.-]+(/[\w.-]+)?", re.ASCII)  # as `kb clarify --by`
_RECORD_ID = re.compile(rf"(?:{REQ_ID}|{EXCL_ID}|{FEAT_ID}|{ADR_ID}|{INV_ID}|{COMP_ID}|{TERM_ID})")
_SHA = re.compile(r"[0-9a-f]{40}")
_KEY = re.compile(r"[0-9a-f]{12}")

Kind = Literal["omission", "entity", "relation", "unfaithful", "granularity", "categories"]
# the decisions each kind of finding takes
ALLOWED: dict[str, tuple[str, ...]] = {
    "omission": ("added", "covered", "not_a_rule", "product_decision"),
    "entity": ("added", "covered", "not_a_rule"),
    "relation": ("added", "covered", "not_a_rule"),
    "unfaithful": ("fixed", "correct"),
    "granularity": ("fixed", "ok"),
    "categories": ("not_a_rule",),
}


def _iso(value) -> str:
    if isinstance(value, datetime.date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise ValueError("must be a date, YYYY-MM-DD")  # noqa: TRY004 - pydantic turns only ValueError into a field error
    datetime.date.fromisoformat(value)
    return value


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str = Field(pattern=rf"^(?:{PUBLIC_ID.pattern}|{CATEGORIES})$")
    canonical: str
    sentence_key: str | None = Field(default=None, pattern=rf"^{_KEY.pattern}$")
    decision: Literal["added", "covered", "not_a_rule", "product_decision", "correct", "fixed", "ok"]
    record: str | None = Field(default=None, pattern=rf"^{_RECORD_ID.pattern}$")
    # the sha256 of the record file's bytes when the person decided: of `record`, or (a correct or ok on a record's
    # finding) of the finding's records (records_sha256). A decision stops applying once they change.
    record_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=MAX_REASON)
    by: str = Field(max_length=MAX_BY)
    date: str

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        return _iso(v)

    @field_validator("by")
    @classmethod
    def _handle(cls, v: str) -> str:
        if not _HANDLE.fullmatch(v):
            raise ValueError("must look like @handle or @org/team (ASCII)")
        return v


class SummaryFinding(BaseModel):
    """One open finding as kb/eval/summary.yaml holds it (findings.finding_dict)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=rf"^{PUBLIC_ID.pattern}$")
    canonical: str
    kind: Literal["omission", "unfaithful", "split", "merge", "unknown"]
    ref: str
    text: str
    severity: Literal["high", "medium", "low"]
    supporters: int
    related: list[str]
    note: str
    kind_hint: Literal["requirement", "decision", "invariant", "exclusion", "entity", "relation"] | None = None
    ends: list[list[str]] = []  # an entity omission's names; a relation omission's source names and target names


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str = Field(pattern=rf"^{_SHA.pattern}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    salt: str = Field(pattern=r"^[0-9a-f]{32}$")
    coverage: dict
    agreement: dict
    open_findings: list[SummaryFinding]
    judged_at: str

    @field_validator("judged_at", mode="before")
    @classmethod
    def _date(cls, v):
        return _iso(v)


# --- reading -----------------------------------------------------------------------------------------------------

def _load(data: bytes, what: str):
    try:
        return load_yaml_strict(data.decode("utf-8"))
    except (UnicodeDecodeError, YamlError) as e:
        raise EvalError(f"{what} does not parse: {inline_code(str(e))}") from e


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())


def parse_summary(data: bytes) -> Summary:
    doc = _load(data, SUMMARY)
    try:
        return Summary.model_validate(doc)
    except ValidationError as e:
        raise EvalError(f"{SUMMARY} is not an evaluation summary: {inline_code(_errors(e))}") from e


def read_summary(root: Path) -> Summary | None:
    """kb/eval/summary.yaml, validated; None when there is none. EvalError when it is unusable (a symlink, not a
    regular file, too large, not a summary)."""
    root = Path(root)
    try:
        data = read_regular(root, SUMMARY, max_bytes=MAX_FILE_BYTES)
    except UnsafePath as e:
        raise EvalError(f"{SUMMARY} is a symlink or an unsafe path; refusing to read it") from e
    if data is None:
        if (root / SUMMARY).exists():
            raise EvalError(f"{SUMMARY} is not a regular file, or larger than {MAX_FILE_BYTES} bytes")
        return None
    return parse_summary(data)


def _entries(data: bytes) -> list[Decision | str]:
    """Each entry of the file, as a Decision or why it does not parse. EvalError when the file itself is unusable."""
    doc = _load(data, DECISIONS)
    if not isinstance(doc, dict) or list(doc) != ["decisions"] or not isinstance(doc["decisions"], list):
        raise EvalError(f"{DECISIONS} must be a mapping with one key, `decisions`, holding a list")
    out: list[Decision | str] = []
    for entry in doc["decisions"]:
        try:
            out.append(Decision.model_validate(entry))
        except ValidationError as e:
            out.append(_errors(e))
    return out


def parse_decisions(data: bytes) -> list[Decision]:
    """Every entry, each well-formed (EvalError on the first that is not): what `kb eval decide` appends to."""
    out = []
    for n, entry in enumerate(_entries(data), 1):
        if isinstance(entry, str):
            raise EvalError(f"{DECISIONS}: decision {n} is invalid: {inline_code(entry)}")
        out.append(entry)
    return out


def read_decisions(root: Path) -> list[Decision]:
    """kb/eval/decisions.yaml, validated; [] when there is none. EvalError when it is unusable."""
    root = Path(root)
    try:
        data = read_regular(root, DECISIONS, max_bytes=MAX_FILE_BYTES)
    except UnsafePath as e:
        raise EvalError(f"{DECISIONS} is a symlink or an unsafe path; refusing to read it") from e
    if data is None:
        if (root / DECISIONS).exists():
            raise EvalError(f"{DECISIONS} is not a regular file, or larger than {MAX_FILE_BYTES} bytes")
        return []
    return parse_decisions(data)


def _invalid(why: str) -> Issue:
    return Issue("eval.decisions_invalid", why, "error", DECISIONS)


def canonical_kind(canonical: str) -> str | None:
    """The kind of finding a canonical id names (as finding_kind), or None when it names none."""
    if canonical == CATEGORIES:
        return "categories"
    return {"S": "omission", "B": "omission", "E": "entity", "N": "relation", "R": "unfaithful", "T": "unfaithful",
            "L": "unfaithful", "M": "granularity"}.get(canonical.partition(":")[0] if ":" in canonical else "")


def _inconsistency(repo: KbRepo, d: Decision, live: set[str]) -> str | None:
    """Why `d` cannot stand as a person's decision, or None: what `kb eval decide` would not have written."""
    kind = canonical_kind(d.canonical)
    if (d.finding == CATEGORIES) != (d.canonical == CATEGORIES) or kind is None:
        return "its finding and canonical id do not name one finding"
    if kind == "categories" and (d.sentence_key or d.record or d.record_sha256):
        return "`categories` carries no sentence key and no record"
    if d.decision not in ALLOWED[kind]:
        return f"`{d.decision}` is not a decision this finding takes"
    prefix, _, rest = d.canonical.partition(":")
    if prefix == "S" and d.sentence_key != rest:
        return "its sentence key is not its finding's"
    if prefix != "S" and prefix != "B" and d.sentence_key is not None:
        return "only a sentence's finding carries a sentence key"
    if d.decision in ("added", "covered"):
        if d.record is None or d.record not in repo.records or d.record not in live:
            return "the record it names does not exist or is not live"
    elif d.decision == "fixed":
        if d.record is None or d.record not in repo.records:
            return "the record it names does not exist"
    elif d.record is not None:
        return f"`{d.decision}` names no record"
    if d.decision in _CHANGING and d.record_sha256 is None:
        return "it has no record_sha256"
    if d.decision in ("correct", "ok") and d.record_sha256 is None:
        return "it has no record_sha256"
    if d.decision in ("not_a_rule", "product_decision") and d.reason is None:
        return "it has no reason"
    if d.reason is not None:
        try:
            _plain("reason", d.reason, MAX_REASON)
        except ClarifyError as e:
            return str(e)
    return None


def check_decisions(repo: KbRepo, entries: list[Decision | str]) -> tuple[list[Decision], list[Issue]]:
    """(the decisions that stand, an eval.decisions_invalid error per entry that does not: malformed, inconsistent
    with itself or the knowledge base, or a second decision on one finding). A dropped entry settles nothing."""
    live = live_ids(repo)
    out: list[Decision] = []
    issues: list[Issue] = []
    seen: set[str] = set()
    for n, d in enumerate(entries, 1):
        if isinstance(d, str):
            why = f"invalid: {inline_code(d)}"
        elif d.finding in seen:
            why = f"{_shown_id(d.finding)} is decided twice; only the first counts"
        else:
            why = _inconsistency(repo, d, live)
            if why is not None:
                why = f"{_shown_id(d.finding)}: {why}"
        if why is not None:
            issues.append(_invalid(f"{DECISIONS}: decision {n} is dropped, it settles nothing: {why}"))
            continue
        seen.add(d.finding)  # type: ignore[union-attr]
        out.append(d)  # type: ignore[arg-type]
    return out, issues


def decisions_of(repo: KbRepo) -> tuple[list[Decision], list[Issue]]:
    """(the decisions kb/eval/decisions.yaml holds in `repo` that stand, an eval.decisions_invalid error per entry
    that does not, or for the whole file). A repository loaded from a ref or the working tree carries the file in
    `raw`; the gates read it from there."""
    data = repo.raw.get(DECISIONS)
    if data is None:
        return [], []
    try:
        entries = _entries(data)
    except EvalError as e:
        return [], [_invalid(f"{e}; no decision in it counts")]
    return check_decisions(repo, entries)


def suppressed_keys(decisions: Iterable[Decision]) -> set[str]:
    return {d.sentence_key for d in decisions if d.decision in SUPPRESSING and d.sentence_key}


def decided_keys(repo_root: Path) -> set[str]:
    """The sentence keys whose decision (one that stands) is added, covered or not_a_rule: `uncovered()` leaves them
    out, so a decided sentence stops warning. kb check reports the entries that do not stand."""
    return suppressed_keys(decisions_of(KbRepo.load(Path(repo_root)))[0])


def record_bytes(repo: KbRepo, rid: str) -> bytes | None:
    return repo.raw.get(repo.paths[rid]) if rid in repo.paths else None


def records_sha256(repo: KbRepo, rids: list[str]) -> str:
    """The sha256 of one record file's bytes; for several, over each id, length and bytes in id order."""
    if len(rids) == 1:
        return hashlib.sha256(record_bytes(repo, rids[0]) or b"").hexdigest()
    h = hashlib.sha256()
    for rid in sorted(rids):
        data = record_bytes(repo, rid) or b""
        h.update(f"{rid} {len(data)}\n".encode())
        h.update(data)
    return h.hexdigest()


# --- findings in the knowledge base ------------------------------------------------------------------------------

def finding_kind(f: SummaryFinding) -> str:
    if f.kind == "omission":
        return f.kind_hint if f.kind_hint in ("entity", "relation") else "omission"
    return "unfaithful" if f.kind == "unfaithful" else "granularity"


def finding_records(f: SummaryFinding) -> list[str]:
    """The kb records an unfaithful or granularity finding is about (R:, T:, L: and M: canonical ids), each a valid
    record id; [] for any other finding."""
    prefix, _, rest = f.canonical.partition(":")
    if prefix in ("R", "T"):
        ids = [rest]
    elif prefix == "L":
        ids = [rest.split(":", 1)[0]]
    elif prefix == "M":
        ids = rest.split("+")
    else:
        return []
    return ids if all(_RECORD_ID.fullmatch(r) for r in ids) else []


def _all_sentences(repo: KbRepo):
    bad = set(bad_segment_ids(repo))
    for src in sorted(set(repo.sources) - bad):
        yield from sentences(repo, src)


def finding_spans(repo: KbRepo, f: SummaryFinding) -> list[tuple[str, int, int]]:
    """Where the finding's text lies in the stored documents now: an S finding's sentences (by sentence key); any
    other finding's passages, found in its segment (else anywhere in its source)."""
    prefix, _, rest = f.canonical.partition(":")
    if prefix == "S" and _KEY.fullmatch(rest):
        return [(s.ref.partition("#")[0], s.start, s.end) for s in _all_sentences(repo) if sentence_key(s) == rest]
    src, _, seg_id = f.ref.partition("#")
    text = repo.source_text(src)
    seg = next((s for s in repo.segments(src) if s.id == seg_id), None)
    out = []
    for passage in f.text.split("\n"):
        at = None
        if seg is not None and (hit := find_quote(text[seg.start:seg.end], passage)) is not None:
            at = (seg.start + hit[0], seg.start + hit[1])
        elif (hit := find_quote(text, passage)) is not None:
            at = hit
        if at is not None:
            out.append((src, *at))
    return out


def span_sentence_key(repo: KbRepo, spans: Iterable) -> str | None:
    """The sentence key of the first sentence (in document order) the spans ([src, start, end]) overlap, or None."""
    spans = [tuple(s) for s in spans]
    by_src: dict[str, list] = {}
    for s in spans:
        by_src.setdefault(s[0], []).append(s)
    for src in sorted(by_src):
        if src not in repo.sources or src in set(bad_segment_ids(repo)):
            continue
        for s in sentences(repo, src):
            if any(max(s.start, a) < min(s.end, b) for _, a, b in by_src[src]):
                return sentence_key(s)
    return None


def finding_sentence_key(repo: KbRepo, f: SummaryFinding) -> str | None:
    """The sentence a decision on an S or B omission settles; None for other findings."""
    prefix, _, rest = f.canonical.partition(":")
    if prefix == "S" and _KEY.fullmatch(rest):
        return rest
    if prefix == "B" and f.kind == "omission":
        return span_sentence_key(repo, finding_spans(repo, f))
    return None


def _carries(repo: KbRepo, d: Decision, f: SummaryFinding) -> bool:
    """Whether an earlier cycle's decision `d` on the same canonical id still decides `f`. Never for a B or N item (run
    B numbers its items from 001 in every run: the same id is another passage; a sentence carries over by its key)
    nor a fixed record (flagged again, it is asked again); for a record's finding (R, T, L, M) only while the
    record files are byte for byte what the person saw."""
    if d.canonical != f.canonical or d.decision == "fixed":
        return False
    prefix = f.canonical.partition(":")[0]
    if prefix in ("B", "N"):
        return False
    if prefix in ("R", "T", "L", "M"):
        rids = finding_records(f)
        return bool(rids) and d.record_sha256 == records_sha256(repo, rids)
    return True


def is_decided(repo: KbRepo, f: SummaryFinding, decisions: list[Decision], keys: set[str] | None = None) -> bool:
    """Whether a person has decided `f`: by its public id (this cycle); by an earlier cycle's decision on its canonical
    id where that carries over (_carries); or, for an S or B omission, by a decision that settled its sentence."""
    if any(d.finding == f.id or _carries(repo, d, f) for d in decisions):
        return True
    if f.kind != "omission" or finding_kind(f) != "omission":
        return False
    key = finding_sentence_key(repo, f)
    return key is not None and key in (suppressed_keys(decisions) if keys is None else keys)


def open_findings(repo: KbRepo, summary: Summary, decisions: list[Decision]) -> list[SummaryFinding]:
    keys = suppressed_keys(decisions)
    return [f for f in summary.open_findings if not is_decided(repo, f, decisions, keys)]


# --- is the summary current? -------------------------------------------------------------------------------------

def is_fresh(root: Path, repo: KbRepo, summary: Summary, decisions: list[Decision]) -> bool:
    """Whether the summary still describes the knowledge base: its fingerprint is the current one, or the only
    changes since the evaluated commit are to records a person's decision on one of this summary's findings named
    (a record added for an omission, or fixed). Anything else (another record, any document text) needs a new
    evaluation."""
    if summary.fingerprint == fingerprint(repo):
        return True
    ids = {f.id for f in summary.open_findings}
    pins = [(d.record, d.record_sha256) for d in decisions if d.finding in ids and d.decision in _CHANGING and d.record]
    # each skipped record is pinned to the bytes the person decided on: a later edit needs a new evaluation
    if not pins or any(sha != records_sha256(repo, [rid]) for rid, sha in pins):
        return False
    touched = {rid for rid, _ in pins}
    try:
        if rev_parse(Path(root), summary.commit) != summary.commit:
            return False
        base = KbRepo.at_ref(Path(root), summary.commit)
    except GitError:
        return False
    return fingerprint(base) == summary.fingerprint and fingerprint(repo, skip=touched) == fingerprint(base, skip=touched)


@dataclass(frozen=True)
class EvalStatus:
    summary: Summary | None
    decisions: list[Decision]
    fresh: bool
    open: list[SummaryFinding]


def eval_status(root: Path, repo: KbRepo) -> EvalStatus:
    """The summary, the decisions that stand (kb/eval/decisions.yaml as `repo` holds it), whether the summary is
    current and its findings no one has decided. EvalError when the summary is unusable."""
    summary = read_summary(root)
    decisions, _ = decisions_of(repo)  # those that stand; kb check reports the others
    if summary is None:
        return EvalStatus(None, decisions, False, [])
    return EvalStatus(summary, decisions, is_fresh(root, repo, summary, decisions), open_findings(repo, summary, decisions))


# --- deciding ----------------------------------------------------------------------------------------------------

def _shown_id(value: str) -> str:
    """A finding id as an error may show it: only a public id or `categories`, never anything else given."""
    return inline_code(value) if PUBLIC_ID.fullmatch(value) or value == CATEGORIES else "this finding id"


def _resolve(summary: Summary | None, fid: str) -> SummaryFinding:
    if not PUBLIC_ID.fullmatch(fid):
        raise EvalError("a finding is named by its public id (F- and 10 hex digits, as `kb todo` and `kb eval report` "
                        "show it) or by `categories`")
    if summary is None:
        raise EvalError(f"no evaluation summary ({SUMMARY}): run `kb eval report --write` first")
    found = next((f for f in summary.open_findings if f.id == fid), None)
    if found is None:
        raise EvalError(f"{inline_code(fid)} is not an open finding of {SUMMARY}")
    return found


def _live(repo: KbRepo, rid: str | None, what: str = "record") -> str:
    if rid is None:
        raise EvalError(f"this decision needs `--record`: the {what} it names")
    if not _RECORD_ID.fullmatch(rid):
        raise EvalError("`--record` is not a record id")
    if rid not in repo.records:
        raise EvalError(f"{rid} does not exist")
    if rid not in live_ids(repo):
        raise EvalError(f"{rid} is not live (rejected or superseded)")
    return rid


def _overlaps(spans: list, others: list) -> bool:
    return any(s[0] == o[0] and max(s[1], o[1]) < min(s[2], o[2]) for s in spans for o in others)


def _evaluated(root: Path, summary: Summary) -> KbRepo:
    """The knowledge base at the evaluated commit."""
    try:
        if rev_parse(Path(root), summary.commit) != summary.commit:
            raise EvalError(f"the evaluated commit of {SUMMARY} is not in this repository")
        return KbRepo.at_ref(Path(root), summary.commit)
    except GitError as e:
        raise EvalError(f"cannot read the evaluated commit of {SUMMARY}") from e


def _names_of(names_: Iterable[str]) -> set[str]:
    return {normalise_name(n) for n in names_}


def _check_added(root: Path, repo: KbRepo, summary: Summary, kind: str, f: SummaryFinding, rid: str | None) -> str:
    spans = finding_spans(repo, f)
    if kind == "omission":
        rid = _live(repo, rid)
        if rid in _evaluated(root, summary).records:
            raise EvalError(f"{rid} existed when the evaluation ran: use a new record, or redraft the existing one and "
                            "decide --as fixed on its finding")
        rec = repo.records[rid]
        if not isinstance(rec, COVERING_TYPES) or rec.evidence is None:
            raise EvalError(f"{rid} quotes nothing a rule is recorded by: add a requirement, exclusion, decision or invariant")
        quoted = [(src, a, b) for _, src, a, b in located_quotes(repo, rec.evidence)]
        if not spans or not _overlaps(spans, quoted):
            raise EvalError(f"{rid} does not quote the finding's sentence: quote it word for word, then decide again")
        return rid
    rid = _live(repo, rid, "TERM")
    term = repo.records[rid]
    if not isinstance(term, Term):
        raise EvalError(f"{rid} is not a glossary term (TERM)")
    prefix, _, rest = f.canonical.partition(":")
    if kind == "entity":
        wanted = _names_of(f.ends[0]) if f.ends else {normalise_name(rest)}
        if prefix != "E" or not wanted & set(names(term)):
            raise EvalError(f"{rid} does not carry the entity's name or alias: add it as the name or an alias")
        found = [q for q, *_ in located_quotes(repo, term.evidence)] if term.evidence is not None else []
        if not any(mentions(q.text, term) for q in found):
            raise EvalError(f"{rid} has no found quote that names it: quote a passage containing its name or an alias")
        return rid
    # a relation: the source TERM holds a relation of the finding's type, to a live term, quoting the finding's passage,
    # and its two ends are the finding's two entities (by entity_key)
    parts = rest.split(":")
    rtype = parts[1] if prefix == "N" and len(parts) == 3 else None
    if len(f.ends) != 2:
        raise EvalError("this finding does not name the two entities of its relation: run the evaluation again")
    source_names, target_names = (_names_of(e) for e in f.ends)
    live = live_ids(repo)
    joined = False
    for r in term.relations:
        target = repo.records.get(r.target)
        if r.type != rtype or r.target not in live or not isinstance(target, Term):
            continue
        quoted = [(src, a, b) for _, src, a, b in located_quotes(repo, Evidence(quotes=[r.quote]))]
        if not (spans and _overlaps(spans, quoted) and mentions(f.text, term) and mentions(f.text, target)):
            continue
        if entity_key(term) & source_names and entity_key(target) & target_names:
            return rid
        joined = True
    if joined:
        raise EvalError(f"{rid}'s relation does not join the finding's two entities: its source and target must be "
                        "the terms the finding names")
    raise EvalError(f"{rid} holds no relation of this finding's type to the term its passage names, quoting that "
                    "passage: add the relation on the source TERM, then decide again")


def _check_fixed(root: Path, repo: KbRepo, summary: Summary, f: SummaryFinding, rid: str | None) -> str:
    named = finding_records(f)
    if not named:
        raise EvalError("this finding names no record kb can check: it cannot be decided --as fixed")
    if rid is not None:
        if not _RECORD_ID.fullmatch(rid):
            raise EvalError("`--record` is not a record id")
        if rid not in named:
            raise EvalError(f"{rid} is not a record this finding is about")
    candidates = [rid] if rid is not None else named
    base = _evaluated(root, summary)
    for c in candidates:
        now = record_bytes(repo, c)
        if now is not None and record_bytes(base, c) != now:  # changed; a deleted record is retired, never deleted
            return c
    raise EvalError(f"{' '.join(candidates)}: not changed since the evaluated commit {summary.commit[:12]}; fix the "
                    "record first, then decide again")


def _reason(value: str | None, required: bool) -> str | None:
    if value is None:
        if required:
            raise EvalError("this decision needs `--reason`: why, in one short plain sentence")
        return None
    try:
        return _plain("--reason", value, MAX_REASON)
    except ClarifyError as e:
        raise EvalError(str(e)) from e


def decide(root: Path, fid: str, decision: str, by: str, record: str | None = None, reason: str | None = None,
           date: str | None = None) -> Decision:
    """Validate a person's decision on finding `fid` and append it to kb/eval/decisions.yaml atomically. EvalError
    (and nothing written) when the finding is not open in the summary, the decision does not fit its kind, or the
    knowledge base does not show it: `added` needs a live record quoting the finding's sentence (a TERM carrying the
    entity's name, or a source TERM holding the relation), `covered` a live record, `fixed` a record changed since the
    evaluated commit, `not_a_rule` a reason."""
    root = Path(root)
    if decision not in DECISION_VALUES:
        raise EvalError(f"--as must be one of {', '.join(DECISION_VALUES)}")
    if len(by) > MAX_BY or not _HANDLE.fullmatch(by):
        raise EvalError(f"--by must look like @handle or @org/team (at most {MAX_BY} characters)")
    try:
        day = _iso(date or datetime.date.today().isoformat())
    except ValueError as e:
        raise EvalError("--date must be a date, YYYY-MM-DD") from e
    why = _reason(reason, decision in ("not_a_rule", "product_decision"))
    decisions = read_decisions(root)
    repo = KbRepo.load(root)
    if fid == CATEGORIES:
        summary, kind, canonical, f = None, "categories", CATEGORIES, None
    else:
        summary = read_summary(root)
        f = _resolve(summary, fid)
        kind, canonical = finding_kind(f), f.canonical
    if any(d.finding == fid for d in decisions):
        raise EvalError(f"{_shown_id(fid)} is decided already; edit {DECISIONS} to change a decision")
    if decision not in ALLOWED[kind]:
        raise EvalError(f"this finding takes one of: {', '.join(ALLOWED[kind])}")
    rid = record
    if decision in ("not_a_rule", "product_decision", "correct", "ok"):
        if record is not None:
            raise EvalError(f"`--as {decision}` takes no `--record`")
    elif decision == "added":
        rid = _check_added(root, repo, summary, kind, f, record)  # type: ignore[arg-type]
    elif decision == "covered":
        rid = _live(repo, record, "TERM" if kind in ("entity", "relation") else "record")
        if kind in ("entity", "relation") and not isinstance(repo.records[rid], Term):
            raise EvalError(f"{rid} is not a glossary term (TERM)")
    elif decision == "fixed":
        rid = _check_fixed(root, repo, summary, f, record)  # type: ignore[arg-type]
    if rid is not None:
        sha = records_sha256(repo, [rid])
    elif f is not None and decision in ("correct", "ok"):
        sha = records_sha256(repo, finding_records(f)) if finding_records(f) else None
    else:
        sha = None
    entry = Decision(finding=fid, canonical=canonical, sentence_key=finding_sentence_key(repo, f) if f else None,
                     decision=decision, record=rid, record_sha256=sha, reason=why, by=by, date=day)  # type: ignore[arg-type]
    if summary is not None:
        standing, _ = decisions_of(repo)
        if not is_fresh(root, repo, summary, [*standing, entry]):
            raise EvalError(f"the knowledge base or its documents changed since {SUMMARY} was written (beyond what this "
                            "and earlier decisions name): run the evaluation again (`kb todo`)")
    check_target(root, DECISIONS, "file")  # never through a symlink (kb/, kb/eval/ or the file)
    data = [d.model_dump() for d in [*decisions, entry]]
    write_atomic(root, DECISIONS, dump_yaml({"decisions": data}))
    return entry
