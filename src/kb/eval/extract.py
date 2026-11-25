"""`kb eval extract`: a blind second extraction (run B) of every stored segment, and the validation of what it wrote.

Run B lives in .kb/cache/eval/run-b/, which the agent writes: kb trusts none of it. Every file is parsed and checked
the way the gates check kb records, bad items are set aside with an `eval.run_b_invalid` issue, and nothing is ever
copied into kb/.
"""

import os
import re
import shutil
from importlib.resources import files
from pathlib import Path
from string import Template

from kb.config.schema import KbConfig
from kb.eval import EvalError
from kb.eval.align import Item, entity_item, found_spans, record_item
from kb.gates.entities import mentions, names_both
from kb.gates.quotes import (
    MAX_QUOTES_PER_CLAIM,
    MIN_QUOTE_CHARS,
    _claimed_text,
    numbers,
    quote_results,
    too_short,
)
from kb.ingest.brief import _example_records, _examples, _segments, next_ids, rules
from kb.issues import Issue
from kb.records.ids import ADR_ID, EXCL_ID, INV_ID, REQ_ID, TERM_ID, parse_id
from kb.records.io import RecordError, parse_record
from kb.records.lifecycle import live_ids
from kb.records.models import Evidence, Term
from kb.records.repo import KbRepo
from kb.safepath import UnsafePath, read_regular
from kb.textfmt import inline_code

EVAL_DIR = ".kb/cache/eval"
RUN_B = ".kb/cache/eval/run-b"
EXTRACT_BRIEF = ".kb/cache/eval/extract.md"
EVAL_BASE = ".kb/cache/eval/base"  # the commit `kb eval extract` ran on
EXTRACT_VERSION = "v1"
DRAFTED_BY = "agent:<your name> via kb eval extract"
B = "B-"  # run B's id prefix: B-REQ-F-001, B-TERM-seat

# run B's directories, laid out like kb/: directory -> (id prefix after B-, extension)
RUN_B_DIRS = {
    "requirements": ("REQ-", ".yaml"),
    "exclusions": ("EXCL-", ".yaml"),
    "decisions": ("ADR-", ".md"),
    "invariants": ("INV-", ".md"),
    "glossary": ("TERM-", ".yaml"),
}
_NUMBERED = ("REQ-F", "REQ-NF", "EXCL", "ADR", "INV")
# where an id of a record type run B drafts starts, to give it the B- prefix in the examples
_DRAFTED_ID = re.compile(rf"(?<![\w-])(?=(?:{REQ_ID}|{EXCL_ID}|{ADR_ID}|{INV_ID}|{TERM_ID})(?![\w-]))")
_REF_FIELDS = ("actor", "entities", "features", "requirements", "supersedes")
_SAFE_REL = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")


# --- the brief ---------------------------------------------------------------------------------------------------

def _run_b_examples() -> list[tuple[str, bytes]]:
    """The ingest examples as run B files: B- ids under RUN_B, without the triage, feature and component examples."""
    out = []
    for path, data in _example_records(DRAFTED_BY):
        parts = path.split("/")
        if len(parts) == 3 and parts[1] in RUN_B_DIRS:
            out.append((f"{RUN_B}/{parts[1]}/{B}{parts[2]}", _DRAFTED_ID.sub(B, data.decode()).encode()))
    return out


def build_extract_brief(root: Path, config: KbConfig) -> str:
    """The blind brief: the ingest rules, run B's destination and B- ids, and every segment of every stored source.
    It holds no record of the knowledge base (no statement, no id); deterministic for the same repo state."""
    repo = KbRepo.load(Path(root))
    if not repo.sources:
        raise EvalError("nothing ingested: run `kb ingest` first, then `kb eval extract`")
    modules = ", ".join(inline_code(m.id) for m in config.modules) or "`MOD-<name>`, named after the part of the product it concerns"
    template = Template(files("kb.eval").joinpath("templates", "extract.md").read_text(encoding="utf-8"))
    return template.substitute(
        version=EXTRACT_VERSION,
        run_b=RUN_B,
        modules=modules,
        rules=rules(DRAFTED_BY),
        next_ids="\n".join(f"- {label}: {B}{nid}" for label, nid in next_ids(repo) if nid.rsplit("-", 1)[0] in _NUMBERED),
        examples=_examples(_run_b_examples()),
        segments=_segments(repo, list(repo.sources)),
    )


def _refuse_symlinks(root: Path, rel: str) -> None:
    here = Path(root)
    for part in rel.split("/"):
        here = here / part
        if here.is_symlink():
            raise UnsafePath(f"{here.relative_to(root)} is a symlink; refusing to use it")


def clear_run_b(root: Path) -> None:
    """Empty RUN_B (create it when missing), never through a symlink."""
    root = Path(root)
    _refuse_symlinks(root, RUN_B)
    target = root / RUN_B
    if target.is_dir():
        shutil.rmtree(target)  # removes links inside the tree, never what they point to
    elif target.exists():
        target.unlink()
    target.mkdir(parents=True)


# --- run B ---------------------------------------------------------------------------------------------------------

def _invalid(rel: str, why: str, record: str | None = None) -> Issue:
    path = f"{RUN_B}/{rel}" if _SAFE_REL.fullmatch(rel) else RUN_B
    return Issue("eval.run_b_invalid", f"{inline_code(rel)}: {why}", "error", path, record)


def _run_b_files(root: Path) -> tuple[list[str], list[Issue]]:
    """Paths under RUN_B (relative to it) of every file, and an issue per symlinked directory; dot files are skipped."""
    _refuse_symlinks(root, RUN_B)
    base = root / RUN_B
    if not base.is_dir():
        return [], []
    found: list[str] = []
    issues: list[Issue] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(dirpath)
        for d in sorted(dirnames):
            if (here / d).is_symlink():
                issues.append(_invalid((here / d).relative_to(base).as_posix(), "a symlink; not read"))
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not (here / d).is_symlink())
        found.extend((here / f).relative_to(base).as_posix() for f in filenames if not f.startswith("."))
    return sorted(found), issues


def _unprefixed(value):
    """`value` with the B- prefix taken off each id in it that has one."""
    if isinstance(value, list):
        return [_unprefixed(v) for v in value]
    if isinstance(value, str) and value.startswith(B):
        try:
            parse_id(value[len(B):])
        except ValueError:
            return value
        return value[len(B):]
    return value


def _strip_prefix(fields: dict, prefixed_targets: list[bool]) -> dict:
    """The fields with B- taken off the id and the ids they name, for schema validation. Records in
    `prefixed_targets`, per relation, whether its target had the prefix."""
    rid = fields.get("id")
    if not isinstance(rid, str) or not rid.startswith(B):
        raise RecordError("its id lacks the B- prefix")
    out = {k: (_unprefixed(v) if k in _REF_FIELDS else v) for k, v in fields.items()}
    out["id"] = rid[len(B):]
    if isinstance(out.get("relations"), list):
        rels = []
        for rel in out["relations"]:
            if isinstance(rel, dict) and "target" in rel:
                prefixed_targets.append(isinstance(rel["target"], str) and rel["target"].startswith(B))
                rel = {**rel, "target": _unprefixed(rel["target"])}
            else:
                prefixed_targets.append(False)
            rels.append(rel)
        out["relations"] = rels
    return out


def _segment_problems(repo: KbRepo, refs: list[str]) -> list[str]:
    out = []
    for ref in refs:
        src, _, seg = ref.partition("#")
        if src not in repo.sources:
            out.append(f"unknown source {src}")
        elif not any(s.id == seg for s in repo.segments(src)):
            out.append(f"unknown segment {ref}")
    return out


def _quote_problems(repo: KbRepo, ev: Evidence) -> list[str]:
    """What keeps `ev` from being verified, by the quote gate's rules (gates.quotes.quote_results, mode included)."""
    out = _segment_problems(repo, [q.source for q in ev.quotes])
    if len(ev.quotes) > MAX_QUOTES_PER_CLAIM:
        out.append(f"{len(ev.quotes)} quotes; at most {MAX_QUOTES_PER_CLAIM} are allowed")
    found, unfound = quote_results(repo, ev)
    if unfound and (ev.mode == "all" or not found):
        for q in unfound:
            if too_short(q.text):
                out.append(f"quote shorter than {MIN_QUOTE_CHARS} characters: {inline_code(q.text)}")
            elif not _segment_problems(repo, [q.source]):
                out.append(f"quote not found in {q.source}: {inline_code(q.text)}")
    return out


def _problems(repo: KbRepo, rec, live: set[str]) -> list[str]:
    if not isinstance(rec, Term) and rec.id in live:
        return [f"{B}{rec.id} is a live kb record's id with {B} taken off; number run B from the next free IDs in the brief"]
    if rec.evidence is None:
        return ["quotes no source; add evidence.quotes from the segment it comes from"]
    out = _quote_problems(repo, rec.evidence)
    found, _ = quote_results(repo, rec.evidence)
    if isinstance(rec, Term):
        if found and not any(mentions(q.text, rec) for q in found):
            out.append("neither the name nor an alias appears in its found quotes")
    elif missing := sorted(numbers(_claimed_text(rec)) - set().union(*(numbers(q.text) for q in found))):
        out.append(f"numbers not in any found quote: {', '.join(missing)}")
    return out


def _parse(root: Path, rel: str, repo: KbRepo, live: set[str]):
    """(record, relation-target prefix flags) for one run B file, or an Issue."""
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] not in RUN_B_DIRS:
        return _invalid(rel, "not a run B record file: write B- records to " + ", ".join(f"{d}/" for d in RUN_B_DIRS))
    folder, name = parts
    if not name.startswith(B):
        return _invalid(rel, "its ID lacks the B- prefix")
    prefix, ext = RUN_B_DIRS[folder]
    if not name[len(B):].startswith(prefix) or not name.endswith(ext):
        return _invalid(rel, f"{folder}/ holds {B}{prefix}…{ext} files")
    try:
        data = read_regular(root, f"{RUN_B}/{rel}")
    except UnsafePath:
        return _invalid(rel, "a symlink or an unsafe path; not read")
    if data is None:
        return _invalid(rel, "not a regular file, or too large")
    flags: list[bool] = []
    try:
        rec = parse_record(name[len(B):], data, transform=lambda f: _strip_prefix(f, flags))
    except RecordError as e:
        return _invalid(rel, f"does not parse: {inline_code(str(e))}")
    if problems := _problems(repo, rec, live):
        return _invalid(rel, "; ".join(problems), B + rec.id)  # type: ignore[union-attr]
    return rec, flags


def _relation_problem(repo: KbRepo, term: Term, rel, prefixed: bool, terms: dict[str, Term]) -> str | None:
    if not prefixed:
        return "the target lacks the B- prefix"
    target = terms.get(B + rel.target)
    if target is None:
        return f"{B}{rel.target} is not a run B term"
    if problems := _quote_problems(repo, Evidence(quotes=[rel.quote])):
        return "; ".join(problems)
    if not names_both(rel.quote.text, term, target):
        return f"the quote does not name both {B}{term.id} and {B}{rel.target}: {inline_code(rel.quote.text)}"
    return None


def load_run_b(root: Path, repo: KbRepo) -> tuple[list[Item], list[Issue]]:
    """The items of run B that pass validation, sorted by id, and an `eval.run_b_invalid` issue per item (file) or
    relation set aside. Item ids keep the B- prefix. Raises UnsafePath when RUN_B or a directory above it is a
    symlink."""
    root = Path(root)
    rels, issues = _run_b_files(root)
    live = live_ids(repo)
    parsed: dict[str, tuple[str, object, list[bool]]] = {}  # B id -> (path under RUN_B, record, target flags)
    for rel in rels:
        got = _parse(root, rel, repo, live)
        if isinstance(got, Issue):
            issues.append(got)
        else:
            rec, flags = got
            parsed[B + rec.id] = (rel, rec, flags)  # type: ignore[attr-defined]
    terms = {iid: rec for iid, (_, rec, _) in parsed.items() if isinstance(rec, Term)}
    items: list[Item] = []
    for iid, (rel, rec, flags) in sorted(parsed.items()):
        spans = found_spans(repo, rec.evidence)  # type: ignore[attr-defined]
        if not isinstance(rec, Term):
            items.append(record_item(iid, rec, spans))  # type: ignore[arg-type]
            continue
        kept = []
        for r, prefixed in zip(rec.relations, flags):
            if why := _relation_problem(repo, rec, r, prefixed, terms):
                issues.append(_invalid(rel, f"relation {r.type} {B}{r.target}: {why}", iid))
            else:
                kept.append(r)
        items.append(entity_item(iid, rec.model_copy(update={"relations": kept}), spans, target_prefix=B))
    return items, sorted(issues, key=lambda i: (i.path or "", i.message))
