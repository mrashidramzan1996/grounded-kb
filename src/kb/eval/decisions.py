"""A person's decisions on the findings of an evaluation (kb/eval/decisions.yaml), and the summary they resolve against.

`kb eval decide` validates a decision against the knowledge base and appends it atomically. A finding is named by its
public id (F-…, from kb/eval/summary.yaml's open_findings, never recomputed) or by the pseudo-finding `categories`. The
items the judge dismissed (summary.yaml's `dismissed`: judged covered, not a rule or not an entity) are settled only by
a person: the pseudo-finding `dismissed` (`--as ok`, pinned to the exact list) agrees with them, and `--as reopen` on
one's public id turns it into an open omission finding of the same id.
A decision that a sentence is added, covered or not a rule keys on the sentence (sentence_key), so it keeps suppressing
that sentence after a later `kb eval judge` cycle gives its finding another public id.
"""

import datetime
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kb.clarify import _HEADER as CLARIFICATIONS_HEADER
from kb.clarify import CLARIFICATIONS, MAX_BY, ClarifyError, _plain
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
from kb.sources.sentences import (
    Sentence,
    sentence_key,
    sentences,
    text_key,
)
from kb.textfmt import inline_code
from kb.yamlio import YamlError, dump_yaml, load_yaml_strict

DECISIONS = "kb/eval/decisions.yaml"
CATEGORIES = "categories"  # the pseudo-finding of the categories ask: no document states some kinds of requirement
DISMISSED = "dismissed"  # the pseudo-finding of the dismissed ask: the judge's dismissals, agreed with as one list
PUBLIC_ID = re.compile(r"F-[0-9a-f]{10}")
DECISION_VALUES = ("added", "covered", "not_a_rule", "product_decision", "correct", "fixed", "ok", "reopen")
SUPPRESSING = frozenset({"added", "covered", "not_a_rule"})  # decisions that settle a sentence: it stops warning
# what changes a record: a decision naming such a record also settles that change (the summary stays current)
_CHANGING = frozenset({"added", "fixed"})
MAX_REASON = 200
MAX_FILE_BYTES = 8 * 1024 * 1024
_HANDLE = re.compile(r"@[\w.-]+(/[\w.-]+)?", re.ASCII)  # as `kb clarify --by`
_RECORD_ID = re.compile(rf"(?:{REQ_ID}|{EXCL_ID}|{FEAT_ID}|{ADR_ID}|{INV_ID}|{COMP_ID}|{TERM_ID})")
_SHA = re.compile(r"[0-9a-f]{40}")
_KEY = re.compile(r"[0-9a-f]{12}")

Kind = Literal["omission", "entity", "relation", "unfaithful", "granularity", "categories", "dismissed", "dismissal"]
# the decisions each kind of finding takes
ALLOWED: dict[str, tuple[str, ...]] = {
    "omission": ("added", "covered", "not_a_rule", "product_decision"),
    "entity": ("added", "covered", "not_a_rule"),
    "relation": ("added", "covered", "not_a_rule"),
    "unfaithful": ("fixed", "correct"),
    "granularity": ("fixed", "ok"),
    "categories": ("not_a_rule",),
    "dismissed": ("ok",),  # the judge's dismissals, as one list: a person agrees
    "dismissal": ("reopen",),  # one dismissed item: a person disagrees, and it becomes an open omission finding
}


def _iso(value) -> str:
    if isinstance(value, datetime.date):
        value = value.isoformat()
    if not isinstance(value, str):
        raise ValueError("must be a date, YYYY-MM-DD")  # noqa: TRY004 - pydantic turns only ValueError into a field error
    datetime.date.fromisoformat(value)
    return value


class SentenceRef(BaseModel):
    """A sentence a decision settles, by its key and its text: the reviewer of kb/eval/decisions.yaml reads the text."""

    model_config = ConfigDict(extra="forbid", strict=True)  # a plain str: never bytes (!!binary) made text

    key: str = Field(pattern=rf"^{_KEY.pattern}$")
    text: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str = Field(pattern=rf"^(?:{PUBLIC_ID.pattern}|{CATEGORIES}|{DISMISSED})$")
    canonical: str
    sentences: list[SentenceRef] = []  # an S or B omission's: every sentence it settles
    decision: Literal["added", "covered", "not_a_rule", "product_decision", "correct", "fixed", "ok", "reopen"]
    record: str | None = Field(default=None, pattern=rf"^{_RECORD_ID.pattern}$")
    # the sha256 of the record file's bytes when the person decided: of `record`, or (a correct or ok on a record's
    # finding) of the finding's records (records_sha256). A decision stops applying once they change. For `dismissed`,
    # the dismissed list it agrees with (dismissed_sha256): a new judge cycle's list is asked again.
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


class DismissedItem(BaseModel):
    """One item the judge dismissed, as kb/eval/summary.yaml holds it (findings.dismissed_dict)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=rf"^{PUBLIC_ID.pattern}$")
    canonical: str
    kind: Literal["omission", "entity", "relation"]
    ref: str
    text: str
    verdict: Literal["covered", "not_a_rule", "not_an_entity"]
    related: list[str]
    note: str
    ends: list[list[str]] = []


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str = Field(pattern=rf"^{_SHA.pattern}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    salt: str = Field(pattern=r"^[0-9a-f]{32}$")
    coverage: dict
    agreement: dict
    open_findings: list[SummaryFinding]
    dismissed: list[DismissedItem] = []  # a summary from before the dismissed list: none
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


@lru_cache(maxsize=4)  # one kb command reads the same summary several times (kb check's gates, kb todo, decide)
def parse_summary(data: bytes) -> Summary:
    """The summary in `data`, validated (EvalError when it is not one). The same object for the same bytes: never
    change it."""
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


NOT_CANONICAL = f"{DECISIONS} is not in kb's canonical form; record decisions with `kb eval decide`"


def _entries(data: bytes) -> list[Decision | str]:
    """Each entry of the file, as a Decision or why it does not parse. EvalError when the file itself is unusable,
    or is not in kb's canonical form: exactly what dump_yaml writes for the parsed decisions (every field, in its
    order, plain scalars), so the text a reviewer reads is the text kb checks."""
    doc = _load(data, DECISIONS)
    if not isinstance(doc, dict) or list(doc) != ["decisions"] or not isinstance(doc["decisions"], list):
        raise EvalError(f"{DECISIONS} must be a mapping with one key, `decisions`, holding a list")
    out: list[Decision | str] = []
    for entry in doc["decisions"]:
        try:
            out.append(Decision.model_validate(entry))
        except ValidationError as e:
            out.append(_errors(e))
    rendered = [e.model_dump() if isinstance(e, Decision) else raw for e, raw in zip(out, doc["decisions"])]
    try:
        canonical = dump_yaml({"decisions": rendered}).encode("utf-8") == data
    except Exception:  # noqa: BLE001 - a value kb's writer cannot write is not canonical either
        canonical = False
    if not canonical:
        raise EvalError(NOT_CANONICAL)
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
    if canonical == DISMISSED:
        return "dismissed"
    return {"S": "omission", "B": "omission", "E": "entity", "N": "relation", "R": "unfaithful", "T": "unfaithful",
            "L": "unfaithful", "M": "granularity"}.get(canonical.partition(":")[0] if ":" in canonical else "")


_PSEUDO = (CATEGORIES, DISMISSED)


def _reopen_problem(d: Decision, dis: "DismissedItem | None") -> str | None:
    """Why a `reopen` entry cannot stand, or None: it names one dismissed item (for one of the current summary's, by
    that item's canonical id), and carries no sentence key, record or hash."""
    if d.finding in _PSEUDO or canonical_kind(d.canonical) not in ("omission", "entity", "relation"):
        return "`reopen` names one item the evaluation dismissed"
    if dis is not None and dis.canonical != d.canonical:
        return "it names an item the current summary dismissed, but not that item's canonical id"
    if d.sentences or d.record or d.record_sha256:
        return "`reopen` carries no sentence key, no record and no record_sha256"
    return _reason_problem(d)


def _reason_problem(d: Decision) -> str | None:
    if d.reason is not None:
        try:
            _plain("reason", d.reason, MAX_REASON)
        except ClarifyError as e:
            return str(e)
    return None


def _inconsistency(repo: KbRepo, d: Decision, live: set[str], f: SummaryFinding | None,
                   dis: "DismissedItem | None" = None) -> str | None:
    """Why `d` cannot stand as a person's decision, or None: what `kb eval decide` would not have written. `f` is the
    current summary's finding of that public id (an open finding, or a dismissed item a person reopened): its
    canonical id must be the entry's, and the decision one it takes. `dis` is the current summary's dismissed item of
    that public id: only `reopen` names one that is not reopened."""
    if d.decision == "reopen":
        return _reopen_problem(d, dis)
    if f is None and dis is not None:
        return "it names an item the evaluation dismissed: reopen it first (`--as reopen`)"
    if f is not None and f.canonical != d.canonical:
        return "it names a finding of the current summary, but not that finding's canonical id"
    kind = finding_kind(f) if f is not None else canonical_kind(d.canonical)
    if (d.finding in _PSEUDO or d.canonical in _PSEUDO) and d.finding != d.canonical or kind is None:
        return "its finding and canonical id do not name one finding"
    if kind in ("categories", "dismissed") and (d.sentences or d.record):
        return f"`{d.finding}` carries no sentence key and no record"
    if kind == "categories" and d.record_sha256:
        return "`categories` carries no sentence key and no record"
    if d.decision not in ALLOWED[kind]:
        return f"`{d.decision}` is not a decision this finding takes"
    if kind == "dismissed":
        return "it pins no dismissed list (record_sha256)" if d.record_sha256 is None else _reason_problem(d)
    prefix, _, rest = d.canonical.partition(":")
    if prefix == "S" and [x.key for x in d.sentences] != [rest]:
        return "its sentence key is not its finding's"
    if prefix != "S" and prefix != "B" and d.sentences:
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
    return _reason_problem(d)


def _sentence_index(repo: KbRepo) -> dict[str, list[Sentence]]:
    """Every sentence of the stored documents, by key, in document order; built once per repository (do not change
    it)."""
    if "sentence_index" not in repo.memo:
        out: dict[str, list[Sentence]] = {}
        for s in _all_sentences(repo):
            out.setdefault(sentence_key(s), []).append(s)
        repo.memo["sentence_index"] = out
    return repo.memo["sentence_index"]


def _sentence_spans(index: dict[str, list[Sentence]], keys: Iterable[str]) -> list[tuple[str, int, int]]:
    return [(s.ref.partition("#")[0], s.start, s.end) for k in keys for s in index.get(k, ())]


def _added_problem(repo: KbRepo, d: Decision, index: dict[str, list[Sentence]], live: set[str],
                   kind: str | None) -> str | None:
    """What keeps an `added` entry from showing what `kb eval decide` checks: the record quotes one of its sentences
    (through found quotes), or the TERM carries the entity and a quote naming it, or holds a relation of that type."""
    rec = repo.records.get(d.record or "")
    _, _, rest = d.canonical.partition(":")
    if kind == "omission":
        if not isinstance(rec, COVERING_TYPES) or rec.evidence is None:
            return "the record it names records no rule"
        quoted = [(src, a, b) for _, src, a, b in located_quotes(repo, rec.evidence)]
        if not _overlaps(_sentence_spans(index, [x.key for x in d.sentences]), quoted):
            return "the record it names does not quote its sentence"
        return None
    if not isinstance(rec, Term):
        return "the record it names is not a glossary term"
    if kind == "entity":
        found = [q for q, *_ in located_quotes(repo, rec.evidence)] if rec.evidence is not None else []
        if normalise_name(rest) not in names(rec) or not any(mentions(q.text, rec) for q in found):
            return "the term it names does not carry the entity's name in a found quote"
        return None
    parts = rest.split(":")
    rtype = parts[1] if len(parts) == 3 else None
    for r in rec.relations:
        if r.type == rtype and r.target in live and isinstance(repo.records.get(r.target), Term) \
                and located_quotes(repo, Evidence(quotes=[r.quote])):
            return None
    return "the term it names holds no quoted relation of that type"


def _summary_of(repo: KbRepo) -> Summary | None:
    data = repo.raw.get(SUMMARY)
    try:
        return parse_summary(data) if data is not None else None
    except EvalError:
        return None


def _documents(repo: KbRepo) -> str:
    """The fingerprint of the stored documents alone (their segment hashes), with no record."""
    return fingerprint(repo, skip=live_ids(repo))


class _Evaluated:
    """The knowledge base at the summary's evaluated commit, read from git at most once, and only when asked."""

    def __init__(self, root: Path | None, repo: KbRepo, summary: Summary | None):
        self.root, self.repo, self.summary = root, repo, summary
        self._base: KbRepo | None = None
        self._read = False
        self._changed: bool | None = None

    def base(self) -> KbRepo | None:
        if not self._read:
            self._read = True
            if self.root is not None and self.summary is not None:
                try:
                    if rev_parse(Path(self.root), self.summary.commit) == self.summary.commit:
                        self._base = KbRepo.at_ref(Path(self.root), self.summary.commit)
                except GitError:
                    self._base = None
        return self._base

    def ids(self) -> set[str] | None:
        base = self.base()
        return set(base.records) if base is not None else None

    def documents_changed(self) -> bool:
        """Whether the stored documents differ from those the summary evaluated (True when that commit cannot be
        read); False with no summary. While they are the same, an entry's sentences are what kb finds for them."""
        if self._changed is None:
            if self.summary is None or self.summary.fingerprint == fingerprint(self.repo):
                self._changed = False
            else:
                base = self.base()
                self._changed = base is None or _documents(base) != _documents(self.repo)
        return self._changed


def _sentences_problem(repo: KbRepo, d: Decision, index: dict[str, list[Sentence]],
                       f: SummaryFinding | None) -> str | None:
    """What an S or B entry's sentences do not match in the documents now: a text that is not, character for
    character, the document's sentence of that key; for a finding of the current summary, not exactly the sentences
    kb finds for it. Either is an inconsistency while the documents are those the summary evaluated; once they
    changed, the entry is stale (check_decisions)."""
    if any(not any(s.text == x.text for s in index[x.key]) for x in d.sentences):
        return "a sentence's text is not the document's, character for character"
    if f is not None and [(x.key, x.text) for x in d.sentences] != [(sentence_key(s), s.text)
                                                                    for s in finding_sentences(repo, f)]:
        return "its sentences are not exactly its finding's"
    return None


def check_decisions(repo: KbRepo, entries: list[Decision | str],
                    root: Path | None = None) -> tuple[list[Decision], list[Issue]]:
    """(the decisions that stand, an eval.decisions_invalid error per entry that does not: malformed, inconsistent
    with itself or the knowledge base, a sentence whose key is not its text's, an `added` record that does not show
    it, or a second decision on one finding). A dropped entry settles nothing.

    An entry naming a finding of the current summary (kb/eval/summary.yaml in `repo`) is checked against that
    finding, never against the canonical id it declares: the two must be equal, an S or B entry lists exactly the
    finding's sentences, a `fixed` names one of the finding's records, and (with `root`) an `added` on an omission
    names a record new since the evaluated commit. Any other entry is an earlier cycle's: it settles only the sentences
    it lists and shows (is_fresh never pins its record; is_decided carries it only by canonical id and record hash).

    An entry is stale, skipped quietly, when one of its sentences is no longer in the documents, or when its
    sentences no longer match while the documents differ from those the summary evaluated: the finding re-opens
    through the next evaluation."""
    live = live_ids(repo)
    index = _sentence_index(repo)
    summary = _summary_of(repo)
    dismissed = {x.id: x for x in summary.dismissed} if summary else {}
    # the findings of the current summary: its open findings, and the dismissed items a person reopened
    current = {f.id: f for f in summary_findings(summary, [e for e in entries if isinstance(e, Decision)])} if summary else {}
    evaluated = _Evaluated(root, repo, summary)
    out: list[Decision] = []
    issues: list[Issue] = []
    seen: set[tuple] = set()
    for n, d in enumerate(entries, 1):
        stale = False
        if isinstance(d, str):
            why = f"invalid: {inline_code(d)}"
        elif _seen_key(d) in seen:
            why = (f"{_shown_id(d.finding)} is reopened twice; only the first counts" if d.decision == "reopen"
                   else f"{_shown_id(d.finding)} is decided twice; only the first counts")
        elif d.decision == "reopen":
            why = _reopen_problem(d, dismissed.get(d.finding))
            if why is None and summary is not None and any(f.id == d.finding for f in summary.open_findings):
                why = "it names an open finding, not an item the evaluation dismissed"
            why = f"{_shown_id(d.finding)}: {why}" if why is not None else None
        else:
            f = current.get(d.finding)  # the finding it names, if the current summary has it
            kind = finding_kind(f) if f is not None else canonical_kind(d.canonical)
            why = _inconsistency(repo, d, live, f, dismissed.get(d.finding))
            if why is None and canonical_kind(d.canonical) == "omission" and not d.sentences:
                why = "it lists no sentence, so it settles nothing"
            if why is None and any(text_key(x.text) != x.key for x in d.sentences):
                why = "a sentence's key is not the key of its text"
            stale = why is None and any(x.key not in index for x in d.sentences)
            if why is None and not stale and canonical_kind(d.canonical) == "omission":
                mismatch = _sentences_problem(repo, d, index, f)
                if mismatch is not None and evaluated.documents_changed():
                    stale = True  # the documents changed under it: the next evaluation asks again
                else:
                    why = mismatch
            records = finding_records(f) if f is not None else canonical_records(d.canonical)
            if why is None and d.decision == "fixed" and d.record not in records:
                why = "the record it names is not one its finding is about"
            if why is None and not stale and d.decision == "added":
                why = _added_problem(repo, d, index, live, kind)
                if (why is None and kind == "omission" and f is not None
                        and (before := evaluated.ids()) is not None and d.record in before):
                    why = "the record it names existed when the evaluation ran"
            if why is not None:
                why = f"{_shown_id(d.finding)}: {why}"
        if why is not None:
            issues.append(_invalid(f"{DECISIONS}: decision {n} is dropped, it settles nothing: {why}"))
            continue
        seen.add(_seen_key(d))  # type: ignore[arg-type]
        if not stale:  # its sentence changed in the document: nothing to settle any more
            out.append(d)  # type: ignore[arg-type]
    return out, issues


def _seen_key(d: Decision) -> tuple:
    """What one decision decides: a finding (once), a dismissed item's reopening (once, besides the decision on the
    finding it becomes), or one dismissed list (once per list)."""
    if d.decision == "reopen":
        return (d.finding, "reopen")
    if d.finding == DISMISSED:
        return (d.finding, d.record_sha256)
    return (d.finding,)


def decisions_of(repo: KbRepo, root: Path | None = None) -> tuple[list[Decision], list[Issue]]:
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
    return check_decisions(repo, entries, root)


def suppressed_keys(decisions: Iterable[Decision]) -> set[str]:
    return {x.key for d in decisions if d.decision in SUPPRESSING for x in d.sentences}


def decided_keys(repo_root: Path) -> set[str]:
    """The sentence keys whose decision (one that stands) is added, covered or not_a_rule: `uncovered()` leaves them
    out, so a decided sentence stops warning. kb check reports the entries that do not stand."""
    return suppressed_keys(decisions_of(KbRepo.load(Path(repo_root)), Path(repo_root))[0])


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
    return canonical_records(f.canonical)


def canonical_records(canonical: str) -> list[str]:
    """finding_records, from a canonical id."""
    prefix, _, rest = canonical.partition(":")
    if prefix in ("R", "T"):
        ids = [rest]
    elif prefix == "L":
        ids = [rest.split(":", 1)[0]]
    elif prefix == "M":
        ids = rest.split("+")
    else:
        return []
    return ids if all(_RECORD_ID.fullmatch(r) for r in ids) else []


def _bad_sources(repo: KbRepo) -> set[str]:
    if "bad_sources" not in repo.memo:
        repo.memo["bad_sources"] = frozenset(bad_segment_ids(repo))
    return repo.memo["bad_sources"]


def _all_sentences(repo: KbRepo):
    for src in sorted(set(repo.sources) - _bad_sources(repo)):
        yield from sentences(repo, src)


def finding_spans(repo: KbRepo, f: SummaryFinding) -> list[tuple[str, int, int]]:
    """Where the finding's text lies in the stored documents now: an S finding's sentences (by sentence key); any
    other finding's passages, found in its segment (else anywhere in its source)."""
    prefix, _, rest = f.canonical.partition(":")
    if prefix == "S" and _KEY.fullmatch(rest):
        return [(s.ref.partition("#")[0], s.start, s.end) for s in _sentence_index(repo).get(rest, ())]
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


def span_sentences(repo: KbRepo, spans: Iterable) -> list[Sentence]:
    """Every sentence the spans ([src, start, end]) overlap, in document order, once per key."""
    by_src: dict[str, list] = {}
    for s in spans:
        by_src.setdefault(s[0], []).append(tuple(s))
    bad = _bad_sources(repo)
    out: dict[str, Sentence] = {}
    for src in sorted(by_src):
        if src not in repo.sources or src in bad:
            continue
        for s in sentences(repo, src):
            if any(max(s.start, a) < min(s.end, b) for _, a, b in by_src[src]):
                out.setdefault(sentence_key(s), s)
    return list(out.values())


def finding_sentences(repo: KbRepo, f: SummaryFinding | None) -> list[Sentence]:
    """The sentences a decision on an S or B omission settles: an S finding's sentence, every sentence a B finding's
    passages overlap; none for other findings."""
    if f is None:
        return []
    prefix, _, rest = f.canonical.partition(":")
    if prefix == "S" and _KEY.fullmatch(rest):
        return _sentence_index(repo).get(rest, [])[:1]
    if prefix == "B" and f.kind == "omission":
        return span_sentences(repo, finding_spans(repo, f))
    return []


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
    """Whether a person has decided `f`: by a decision on its canonical id, of a kind `f` takes, naming its public id
    (this cycle) or carrying over from an earlier cycle (_carries); or, for an S or B omission, when every sentence it
    covers is settled by a decision (added, covered, not a rule) listing that sentence."""
    kind = finding_kind(f)
    for d in decisions:
        if d.canonical == f.canonical and d.decision in ALLOWED[kind] and (d.finding == f.id or _carries(repo, d, f)):
            return True
    if kind != "omission":
        return False
    found = [sentence_key(s) for s in finding_sentences(repo, f)]
    settled = suppressed_keys(decisions) if keys is None else keys
    return bool(found) and all(k in settled for k in found)


def reopened_finding(x: DismissedItem) -> SummaryFinding:
    """The open omission finding a dismissed item becomes once a person reopens it: same public and canonical id."""
    note = f"the evaluation agent judged it {x.verdict.replace('_', ' ')}; a person reopened it"
    return SummaryFinding(id=x.id, canonical=x.canonical, kind="omission", ref=x.ref, text=x.text, severity="medium",
                          supporters=1, related=x.related, note=note + (f": {x.note}" if x.note else ""),
                          kind_hint=x.kind if x.kind in ("entity", "relation") else None, ends=x.ends)


def reopened_ids(summary: Summary, decisions: Iterable[Decision]) -> set[str]:
    """The summary's dismissed items a `reopen` decision (one that stands) names."""
    dismissed = {x.id: x for x in summary.dismissed}
    return {d.finding for d in decisions
            if d.decision == "reopen" and d.finding in dismissed and _reopen_problem(d, dismissed[d.finding]) is None}


def summary_findings(summary: Summary, decisions: Iterable[Decision]) -> list[SummaryFinding]:
    """Every finding of the summary: its open findings, then the dismissed items a person reopened."""
    ids = reopened_ids(summary, decisions)
    return [*summary.open_findings, *(reopened_finding(x) for x in summary.dismissed if x.id in ids)]


def open_findings(repo: KbRepo, summary: Summary, decisions: list[Decision]) -> list[SummaryFinding]:
    keys = suppressed_keys(decisions)
    return [f for f in summary_findings(summary, decisions) if not is_decided(repo, f, decisions, keys)]


def dismissed_sha256(items: Iterable[DismissedItem]) -> str:
    """The pin of a `dismissed` decision: sha256 over the dismissed items' public ids and texts, sorted by id."""
    return hashlib.sha256(json.dumps(sorted([x.id, x.text] for x in items), ensure_ascii=False).encode()).hexdigest()


def dismissed_agreed(summary: Summary, decisions: Iterable[Decision]) -> bool:
    """Whether a person agreed with exactly this summary's dismissed list (vacuously, when it dismissed nothing)."""
    pin = dismissed_sha256(summary.dismissed)
    return not summary.dismissed or any(d.finding == DISMISSED and d.decision == "ok" and d.record_sha256 == pin
                                        for d in decisions)


def dismissed_open(summary: Summary, decisions: Iterable[Decision]) -> list[DismissedItem]:
    """The dismissed items no one has settled: not reopened, and the list not agreed with."""
    decisions = list(decisions)
    if dismissed_agreed(summary, decisions):
        return []
    ids = reopened_ids(summary, decisions)
    return [x for x in summary.dismissed if x.id not in ids]


# --- is the summary current? -------------------------------------------------------------------------------------

def is_fresh(root: Path, repo: KbRepo, summary: Summary, decisions: list[Decision]) -> bool:
    """Whether the summary still describes the knowledge base: its fingerprint is the current one, or the only
    changes since the evaluated commit are to records a person's decision on one of this summary's findings named
    (a record added for an omission, or fixed), and the new sections of docs/clarifications.md those records quote
    (the person's answer, recorded with `kb clarify`). Anything else (another record, any other document text) needs
    a new evaluation."""
    if summary.fingerprint == fingerprint(repo):
        return True
    by_id = {f.id: f for f in summary_findings(summary, decisions)}
    # only an entry consistent with the finding it names pins: that finding's canonical id, and for `fixed` one of its
    # records; an earlier cycle's entry (its id in no current finding) never does
    pins: list[tuple[Decision, SummaryFinding]] = []
    for d in decisions:
        f = by_id.get(d.finding)
        if f is None or d.canonical != f.canonical or d.decision not in _CHANGING or not d.record:
            continue
        if d.decision == "fixed" and d.record not in finding_records(f):
            continue
        pins.append((d, f))
    # each skipped record is pinned to the bytes the person decided on: a later edit needs a new evaluation
    if not pins or any(d.record_sha256 != records_sha256(repo, [d.record]) for d, _ in pins):  # type: ignore[list-item]
        return False
    try:
        if rev_parse(Path(root), summary.commit) != summary.commit:
            return False
        base = KbRepo.at_ref(Path(root), summary.commit)
    except GitError:
        return False
    # a record added for an omission is new since the evaluated commit: an existing one edited needs a new evaluation
    if any(d.decision == "added" and finding_kind(f) == "omission" and d.record in base.records for d, f in pins):
        return False
    touched = {d.record for d, _ in pins if d.record}
    answers = _answer_segments(repo, base, touched)
    return (fingerprint(base) == summary.fingerprint
            and fingerprint(repo, skip=touched, skip_segments=answers) == fingerprint(base, skip=touched))


_DECIDED_LINE = re.compile(r"^Decided by [^\n]* on [^\n]*\.[ \t]*$", re.MULTILINE)


def _answer_segments(repo: KbRepo, base: KbRepo, rids: set[str]) -> set[tuple[str, str]]:
    """The segments of docs/clarifications.md (kb clarify's file of a person's answers) that the freshness fingerprint
    leaves out, with the file's fixed header: a section that is new since the evaluated commit (absent there by segment
    id and by hash; a rewritten section never is) and whose answer text (after the `Decided by ... on ....` line)
    the quotes of the records `rids` (pinned by a decision) cover character for character, whitespace aside, so that
    all of it was decided on. Any other section, fenced text or rule-less wording included, needs a new evaluation."""
    src = next((sid for sid, s in repo.sources.items() if s.original == CLARIFICATIONS), None)
    if src is None:
        return set()
    quoted = [(a, b) for rid in sorted(rids) if (ev := getattr(repo.records.get(rid), "evidence", None)) is not None
              for _, s, a, b in located_quotes(repo, ev) if s == src]
    if not quoted:
        return set()
    old = base.segments(src) if src in base.sources else []
    ids, hashes = {seg.id for seg in old}, {seg.hash for seg in old}
    text = repo.source_text(src)
    out: set[tuple[str, str]] = set()
    for seg in repo.segments(src):
        if seg.id in ids or seg.hash in hashes:
            continue
        body = text[seg.start:seg.end]
        if body.strip() == CLARIFICATIONS_HEADER.strip():
            out.add((src, seg.id))
            continue
        # the answer is what follows the `Decided by ... on ....` line kb clarify writes under the heading
        m = _DECIDED_LINE.search(body)
        if m is None:
            continue
        covered = [False] * len(body)
        for a, b in quoted:
            for i in range(max(a, seg.start) - seg.start, min(b, seg.end) - seg.start):
                covered[i] = True
        answer = range(m.end(), len(body))
        if any(not body[i].isspace() for i in answer) and all(covered[i] or body[i].isspace() for i in answer):
            out.add((src, seg.id))
    return out


@dataclass(frozen=True)
class EvalStatus:
    summary: Summary | None
    decisions: list[Decision]
    fresh: bool
    open: list[SummaryFinding]
    findings: list[SummaryFinding] = field(default_factory=list)  # every finding: open ones and reopened dismissals
    dismissed: list[DismissedItem] = field(default_factory=list)  # the dismissed items no one has settled yet


def eval_status(root: Path, repo: KbRepo) -> EvalStatus:
    """The summary, the decisions that stand (kb/eval/decisions.yaml as `repo` holds it), whether the summary is
    current, its findings no one has decided, and its dismissed items no one has settled. EvalError when the summary is
    unusable."""
    summary = read_summary(root)
    decisions, _ = decisions_of(repo, root)  # those that stand; kb check reports the others
    if summary is None:
        return EvalStatus(None, decisions, False, [])
    return EvalStatus(summary, decisions, is_fresh(root, repo, summary, decisions), open_findings(repo, summary, decisions),
                      summary_findings(summary, decisions), dismissed_open(summary, decisions))


# --- deciding ----------------------------------------------------------------------------------------------------

def _shown_id(value: str) -> str:
    """A finding id as an error may show it: only a public id or `categories`, never anything else given."""
    return inline_code(value) if PUBLIC_ID.fullmatch(value) or value in _PSEUDO else "this finding id"


def _summary_or_fail(summary: Summary | None) -> Summary:
    if summary is None:
        raise EvalError(f"no evaluation summary ({SUMMARY}): run `kb eval report --write` first")
    return summary


def _resolve(summary: Summary | None, fid: str, decisions: list[Decision]) -> SummaryFinding:
    """The finding `fid` names: an open finding of the summary, or a dismissed item a person reopened."""
    if not PUBLIC_ID.fullmatch(fid):
        raise EvalError("a finding is named by its public id (F- and 10 hex digits, as `kb todo` and `kb eval report` "
                        "show it), by `categories` or by `dismissed`")
    summary = _summary_or_fail(summary)
    found = next((f for f in summary_findings(summary, decisions) if f.id == fid), None)
    if found is None and any(x.id == fid for x in summary.dismissed):
        raise EvalError(f"{inline_code(fid)} is an item the evaluation agent dismissed: reopen it first "
                        f"(`kb eval decide {fid} --as reopen`), or agree with every dismissal "
                        "(`kb eval decide dismissed --as ok`)")
    if found is None:
        raise EvalError(f"{inline_code(fid)} is not an open finding of {SUMMARY}")
    return found


def _dismissal(summary: Summary | None, fid: str) -> DismissedItem:
    """The dismissed item `fid` names, for `--as reopen`."""
    if not PUBLIC_ID.fullmatch(fid):
        raise EvalError("`--as reopen` names one dismissed item by its public id (F- and 10 hex digits, as "
                        "`kb todo` and `kb eval status` show it)")
    summary = _summary_or_fail(summary)
    found = next((x for x in summary.dismissed if x.id == fid), None)
    if found is None:
        raise EvalError(f"{inline_code(fid)} is not an item the evaluation dismissed in {SUMMARY}; `--as reopen` "
                        "takes only those")
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
    evaluated commit, `not_a_rule` a reason. `dismissed --as ok` agrees with the summary's whole dismissed list (pinned
    to it); `--as reopen` names one dismissed item, which becomes an open omission finding of the same id."""
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
    standing, _ = decisions_of(repo, root)
    pin: str | None = None
    if fid == CATEGORIES:
        summary, kind, canonical, f = None, "categories", CATEGORIES, None
    elif fid == DISMISSED:
        summary, kind, canonical, f = _summary_or_fail(read_summary(root)), "dismissed", DISMISSED, None
        if not summary.dismissed:
            raise EvalError("the evaluation dismissed nothing: there is nothing to agree with")
        pin = dismissed_sha256(summary.dismissed)
    elif decision == "reopen":
        summary, kind, f = read_summary(root), "dismissal", None
        canonical = _dismissal(summary, fid).canonical
    else:
        summary = read_summary(root)
        f = _resolve(summary, fid, standing)
        kind, canonical = finding_kind(f), f.canonical
    if decision == "reopen" and any(d.finding == fid and d.decision == "reopen" for d in decisions):
        raise EvalError(f"{_shown_id(fid)} is reopened already; decide the finding it became")
    if fid == DISMISSED and any(d.finding == fid and d.record_sha256 == pin for d in decisions):
        raise EvalError(f"`dismissed` is decided already for this list; edit {DECISIONS} to change a decision")
    if decision != "reopen" and fid != DISMISSED and any(d.finding == fid and d.decision != "reopen" for d in decisions):
        raise EvalError(f"{_shown_id(fid)} is decided already; edit {DECISIONS} to change a decision")
    if decision not in ALLOWED[kind]:
        raise EvalError(f"this finding takes one of: {', '.join(ALLOWED[kind])}")
    rid = record
    if decision in ("not_a_rule", "product_decision", "correct", "ok", "reopen"):
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
    if pin is not None:
        sha = pin
    elif rid is not None:
        sha = records_sha256(repo, [rid])
    elif f is not None and decision in ("correct", "ok"):
        sha = records_sha256(repo, finding_records(f)) if finding_records(f) else None
    else:
        sha = None
    settles = [SentenceRef(key=sentence_key(x), text=x.text) for x in finding_sentences(repo, f)]
    if kind == "omission" and not settles:
        raise EvalError("this finding's text is no longer a sentence of the documents: run the evaluation again (`kb todo`)")
    entry = Decision(finding=fid, canonical=canonical, sentences=settles,
                     decision=decision, record=rid, record_sha256=sha, reason=why, by=by, date=day)  # type: ignore[arg-type]
    if summary is not None and not is_fresh(root, repo, summary, [*standing, entry]):
        raise EvalError(f"the knowledge base or its documents changed since {SUMMARY} was written (beyond what this "
                        "and earlier decisions name): run the evaluation again (`kb todo`)")
    check_target(root, DECISIONS, "file")  # never through a symlink (kb/, kb/eval/ or the file)
    data = [d.model_dump() for d in [*decisions, entry]]
    write_atomic(root, DECISIONS, dump_yaml({"decisions": data}))
    return entry
