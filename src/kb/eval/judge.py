"""`kb eval judge`: the items an evaluation agent judges one at a time, its brief, and the validation of its verdicts.

kb writes the item list (JUDGE_ITEMS) and the brief; the agent writes VERDICTS. All three live in .kb/cache/eval/,
which the agent can write, so kb trusts none of them: the items are recomputed and compared, and every verdict is
checked against the item it answers (its span word for word in the item's text, its values from the listed sets, the
IDs it names live). The brief shows no verdict and never says which extraction an item came from.
"""

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from kb.cache import remove_cache, write_cache
from kb.eval import EvalError
from kb.eval.align import Alignment, align, kb_items
from kb.eval.extract import EVAL_BASE, load_run_b
from kb.gates.coverage import uncovered
from kb.gates.entities import mentions, names_both
from kb.gates.quotes import MIN_QUOTE_CHARS, located_quotes, too_short
from kb.gitfiles import head_commit
from kb.graph import entity_candidates
from kb.ingest.brief import INJECTION_FLAG, _fence, recorded_base
from kb.issues import Issue
from kb.quotes.match import find_quote, fold
from kb.records.lifecycle import live_ids
from kb.records.models import Evidence, Exclusion, Quote, Requirement, Term
from kb.records.repo import KbRepo
from kb.safepath import read_regular
from kb.sources.guard import has_injection
from kb.sources.sentences import sentence_key
from kb.textfmt import inline_code
from kb.yamlio import YamlError, load_yaml_strict

JUDGE_BRIEF = ".kb/cache/eval/judge.md"
VERDICTS = ".kb/cache/eval/verdicts.yaml"
JUDGE_ITEMS = ".kb/cache/eval/items.json"  # the items kb asked about, written by kb
JUDGE_VERSION = "v1"
MAX_ITEMS_BYTES = 64 * 1024 * 1024
MAX_VERDICTS_BYTES = 8 * 1024 * 1024
MAX_NOTE = 1000

# the kinds, in the order the brief lists them; `uncovered` and `only_b` are one question (the judge is not told which)
KINDS = ("faithful", "uncovered", "only_b", "split_merge", "entity_faithful", "relation_faithful", "entity_missing",
         "relation_missing")
_GROUP = {"faithful": 0, "uncovered": 1, "only_b": 1, "split_merge": 2, "entity_faithful": 3, "relation_faithful": 4,
          "entity_missing": 5, "relation_missing": 6}
YES_NO = {
    "faithful": ("same_meaning", "same_actor", "same_modality", "no_added_constraint"),
    "entity_faithful": ("definition_matches_quote", "kind_correct"),
    "relation_faithful": ("quote_states_relation", "direction_correct"),
}
GAP_VERDICTS = ("missing", "covered", "not_a_rule", "unknown")
SPLIT_VERDICTS = ("ok", "should_split", "should_merge", "unknown")
ENTITY_VERDICTS = ("missing", "covered", "not_an_entity", "unknown")
KIND_HINTS = ("requirement", "decision", "invariant", "exclusion")
SEVERITIES = ("high", "medium", "low")
_VERDICT_VALUES = {"uncovered": GAP_VERDICTS, "only_b": GAP_VERDICTS, "split_merge": SPLIT_VERDICTS,
                   "entity_missing": ENTITY_VERDICTS, "relation_missing": ENTITY_VERDICTS}
_GAPS = ("uncovered", "only_b")
_ENTITY_GAPS = ("entity_missing", "relation_missing")
_FIELDS = {kind: ("id", "span", "note", *YES_NO.get(kind, ()),
                  *(("verdict",) if kind in _VERDICT_VALUES else ()),
                  *(("kind_hint", "severity") if kind in _GAPS else ()),
                  *(("related",) if kind in _GAPS + _ENTITY_GAPS else ()))
           for kind in KINDS}


# --- items -------------------------------------------------------------------------------------------------------

def _seg_ref(repo: KbRepo, src: str, offset: int) -> str:
    """The "SRC-x#seg-NN" ref of the segment holding `offset` of the source's text.md."""
    return next((f"{src}#{s.id}" for s in repo.segments(src) if s.start <= offset < s.end), src)


def _passage(repo: KbRepo, spans: list[list]) -> str:
    return "\n".join(repo.source_text(src)[a:b] for src, a, b in spans)


def _quoted(repo: KbRepo, ev: Evidence) -> tuple[list[list], str, str]:
    """(spans, text, ref) of `ev`: the document text of its found quotes (gates.quotes.located_quotes), or, when none is
    found, its quotes as written."""
    located = sorted(located_quotes(repo, ev), key=lambda x: (x[1], x[2], x[3]))
    if located:
        spans = [[src, a, b] for _, src, a, b in located]
        return spans, _passage(repo, spans), located[0][0].source
    return [], "\n".join(q.text for q in ev.quotes), ev.quotes[0].source


def _item(iid: str, kind: str, ref: str, text: str, context: str, spans: list[list], **extra) -> dict:
    return {"id": iid, "kind": kind, "ref": ref, "text": text, "context": context, "spans": spans, **extra}


def _records(repo: KbRepo, live: set[str]) -> list[dict]:
    """R: every live requirement and exclusion."""
    out = []
    for rid, rec in sorted(repo.records.items()):
        if rid not in live or not isinstance(rec, (Requirement, Exclusion)):
            continue
        spans, text, ref = _quoted(repo, rec.evidence)
        lines = [f"{'requirement' if isinstance(rec, Requirement) else 'exclusion (out of scope)'} {rid}", f"statement: {rec.statement}"]
        if isinstance(rec, Requirement):
            lines += [f"modality: {rec.modality or 'not set'}", f"actor: {rec.actor or 'not set'}"]
        out.append(_item(f"R:{rid}", "faithful", ref, text, "\n".join(lines), spans))
    return out


def _gaps(repo: KbRepo, alignment: Alignment, decided: set[str] | frozenset[str]) -> list[dict]:
    """S: every uncovered rule-like sentence; B: every run B record no kb record matches (its quoted text and
    statement, nothing more)."""
    out = []
    for s in uncovered(repo, decided):
        src, _, seg_id = s.ref.partition("#")
        seg = next(x for x in repo.segments(src) if x.id == seg_id)
        out.append(_item(f"S:{sentence_key(s)}", "uncovered", s.ref, s.text, repo.source_text(src)[seg.start:seg.end],
                         [[src, s.start, s.end]]))
    for b in alignment.only_b:
        if b.spans:  # run B items are validated: each has a found quote
            spans = [list(x) for x in b.spans]
            out.append(_item(f"B:{b.id}", "only_b", _seg_ref(repo, spans[0][0], spans[0][1]), _passage(repo, spans),
                             f"statement: {b.statement}", spans))
    return out


def _granularity(repo: KbRepo, alignment: Alignment) -> list[dict]:
    """M: every split (M:<rid>) and merge (M:<rid>+<rid>…), shown as the kb records only."""
    kb = {i.id: i for i in kb_items(repo) if i.kind != "entity"}
    groups = [([k], f"This record's passage may hold {len(bs)} separate rules. Should it be split into one record per rule?")
              for k, bs in alignment.splits]
    groups += [(ks, f"These {len(ks)} records may state one rule. Should they be merged into one record?")
               for ks, _ in alignment.merges]
    out = []
    for ids, question in groups:
        recs = [kb[k] for k in ids if k in kb and kb[k].spans]
        if len(recs) != len(ids):
            continue
        spans = sorted({tuple(s) for r in recs for s in r.spans})
        lines = [line for r in recs for line in (f"{r.kind} {r.id}", f"statement: {r.statement}")]
        out.append(_item("M:" + "+".join(ids), "split_merge", _seg_ref(repo, spans[0][0], spans[0][1]),
                         _passage(repo, [list(s) for s in spans]), "\n".join([*lines, "", question]), [list(s) for s in spans]))
    return out


def _entities(repo: KbRepo, live: set[str]) -> list[dict]:
    """T: every live term with evidence; L: every relation of a live term."""
    out = []
    for tid, term in sorted(repo.records.items()):
        if tid not in live or not isinstance(term, Term):
            continue
        if term.evidence is not None:
            spans, text, ref = _quoted(repo, term.evidence)
            context = (f"term {tid}\nname: {term.name}\naliases: {', '.join(term.aliases) or 'none'}\n"
                       f"kind: {term.kind or 'not set'}\ndefinition: {term.definition}")
            out.append(_item(f"T:{tid}", "entity_faithful", ref, text, context, spans))
        for r in term.relations:
            spans, text, ref = _quoted(repo, Evidence(quotes=[r.quote]))
            target = repo.records.get(r.target)
            tname = target.name if isinstance(target, Term) else "not a term"
            context = f"source: {tid} ({term.name})\nrelation: {r.type}\ntarget: {r.target} ({tname})"
            out.append(_item(f"L:{tid}:{r.type}:{r.target}", "relation_faithful", ref, text, context, spans))
    return out


def _entity_gaps(repo: KbRepo, alignment: Alignment) -> list[dict]:
    """E: every run B entity no kb term matches, and every entity candidate kb detects, one item per key (a candidate
    named by such an entity joins its item); N: every run B relation no kb relation matches."""
    found: dict[str, dict] = {}
    for b in alignment.only_b_entities:
        if b.names and b.spans and b.names[0] not in found:
            spans = [list(x) for x in b.spans]
            context = f"name: {b.names[0]}\naliases: {', '.join(b.names[1:]) or 'none'}\ndefinition: {b.statement}"
            found[b.names[0]] = _item(f"E:{b.names[0]}", "entity_missing", _seg_ref(repo, spans[0][0], spans[0][1]),
                                      _passage(repo, spans), context, spans, names=list(b.names), detected=False)
    for phrase, ref, s in entity_candidates(repo):
        owner = next((e for e in found.values() if phrase in e["names"]), None)
        if owner is not None:
            owner["detected"] = True
        else:
            found[phrase] = _item(f"E:{phrase}", "entity_missing", ref, s.text, f"name: {phrase}",
                                  [[ref.partition("#")[0], s.start, s.end]], names=[phrase], detected=True)
    out = list(found.values())
    for (src, rtype, tgt), rq in zip(alignment.only_b_relations, alignment.only_b_relation_quotes, strict=True):
        spans, text, ref = _quoted(repo, Evidence(quotes=[Quote(source=rq.ref, text=rq.quote)]))
        out.append(_item(f"N:{src}:{rtype}:{tgt}", "relation_missing", ref, text,
                         f"source: {src}\nrelation: {rtype}\ntarget: {tgt}", spans,
                         ends=[list(rq.source_names), list(rq.target_names)]))
    return out


def _order(item: dict) -> tuple:
    """By question, then document order (source, offset), then id: S: and B: items interleave."""
    pos = item["spans"][0] if item["spans"] else [item["ref"].partition("#")[0], -1]
    return _GROUP[item["kind"]], pos[0], pos[1], item["id"]


def judge_items(repo: KbRepo, alignment: Alignment, decided: set[str] | frozenset[str]) -> list[dict]:
    """The items the judge answers, each {id, kind, ref, text, context, spans} (entity gaps also carry `names` and
    `detected`, relation gaps `ends`), sorted by question, document position and id; one item per id. `decided` holds
    the sentence keys a person has decided, which are not asked again."""
    live = live_ids(repo)
    out: dict[str, dict] = {}
    for item in (*_records(repo, live), *_gaps(repo, alignment, decided), *_granularity(repo, alignment),
                 *_entities(repo, live), *_entity_gaps(repo, alignment)):
        out.setdefault(item["id"], item)
    return sorted(out.values(), key=_order)


def _decided(root: Path) -> frozenset[str]:
    """The sentence keys a person has decided (kb/eval/decisions.yaml, Task 9); none yet."""
    return frozenset()


@dataclass(frozen=True)
class JudgeState:
    """The items as kb computes them now, with the alignment they come from."""

    items: list[dict]
    alignment: Alignment
    kb_count: int  # records (not entities) of each side, for agreement()
    b_count: int
    run_b_issues: list[Issue]


def judge_state(root: Path, repo: KbRepo) -> JudgeState:
    b_items, issues = load_run_b(Path(root), repo)
    kb = kb_items(repo)
    al = align(kb, b_items)
    return JudgeState(judge_items(repo, al, _decided(Path(root))), al, sum(i.kind != "entity" for i in kb),
                      sum(i.kind != "entity" for i in b_items), issues)


def items_json(items: list[dict]) -> str:
    return json.dumps(items, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def require_eval_base(root: Path) -> str:
    """The commit `kb eval extract` ran on, which must be HEAD: run B is of that commit's documents."""
    base = recorded_base(Path(root), EVAL_BASE)
    if base is None:
        raise EvalError("no second extraction of this repository: run `kb eval extract` first")
    if base != head_commit(Path(root)):
        raise EvalError(f"HEAD moved since `kb eval extract` ran on {base[:12]}: run `kb eval extract` again")
    return base


def write_judge(root: Path, repo: KbRepo) -> JudgeState:
    """Write JUDGE_ITEMS and the brief, and remove the verdicts of an earlier brief."""
    state = judge_state(root, repo)
    write_cache(Path(root), JUDGE_ITEMS, items_json(state.items))
    write_cache(Path(root), JUDGE_BRIEF, build_judge_brief(repo, state.items))
    remove_cache(Path(root), VERDICTS)
    return state


# --- the brief ---------------------------------------------------------------------------------------------------

_ANSWER = {
    "faithful": "`same_meaning`, `same_actor`, `same_modality`, `no_added_constraint` (each yes or no), `span`, `note`",
    "uncovered": "`verdict`, `kind_hint` and `severity` (when missing), `related`, `span`, `note`",
    "only_b": "`verdict`, `kind_hint` and `severity` (when missing), `related`, `span`, `note`",
    "split_merge": "`verdict`, `span`, `note`",
    "entity_faithful": "`definition_matches_quote`, `kind_correct` (each yes or no), `span`, `note`",
    "relation_faithful": "`quote_states_relation`, `direction_correct` (each yes or no), `span`, `note`",
    "entity_missing": "`verdict`, `related`, `span` (naming the entity when missing), `note`",
    "relation_missing": "`verdict`, `related`, `span` (naming both entities when missing), `note`",
}


def _flagged_segments(repo: KbRepo) -> set[str]:
    return {f"{src}#{seg}" for src, s in repo.sources.items() for seg in s.guard.injection_segments}


def _block(label: str, text: str) -> str:
    fence = _fence(text)
    return f"{label}:\n\n{fence}text\n{text}\n{fence}"


def _render_item(repo: KbRepo, item: dict, flagged: set[str]) -> str:
    refs = {item["ref"], *(_seg_ref(repo, src, a) for src, a, _ in item["spans"])}
    lines = [f"### {inline_code(item['id'])} · {inline_code(item['ref'])}"]
    if refs & flagged or has_injection(item["text"]) or has_injection(item["context"]):
        lines.append(INJECTION_FLAG)
    lines += ["", _block("Text", item["text"]), "", _block("Context", item["context"]), "", f"Answer: {_ANSWER[item['kind']]}."]
    return "\n".join(lines)


def build_judge_brief(repo: KbRepo, items: list[dict]) -> str:
    """The brief: the rules, the questions per item prefix, and every item with its text and context fenced (untrusted
    text cannot close its fence) and instruction-like text flagged. Deterministic for the same items."""
    flagged = _flagged_segments(repo)
    text = files("kb.eval").joinpath("templates", "judge.md").read_text(encoding="utf-8")
    return Template(text).substitute(
        version=JUDGE_VERSION,
        verdicts=VERDICTS,
        count=len(items),
        min_span=MIN_QUOTE_CHARS,
        items="\n\n".join(_render_item(repo, i, flagged) for i in items) or "- none",
    )


# --- verdicts ----------------------------------------------------------------------------------------------------

class _Bad(Exception):
    """Why a verdict is set aside (the message is already escaped)."""


def _shown(value) -> str:
    return "nothing" if value is None else inline_code(str(value))


def _one_of(field: str, value, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise _Bad(f"`{field}` must be one of {', '.join(allowed)}, not {_shown(value)}")
    return value


def _names_term(names: list[str]) -> Term:
    """A stand-in term with these (normalised) names, for gates.entities' name checks."""
    return Term.model_construct(name=names[0] if names else "", aliases=list(names[1:]))


def _span(item: dict, span) -> str:
    if not isinstance(span, str) or not span.strip():
        raise _Bad("needs a `span` copied word for word from the item's text")
    whole = fold(span)[0].strip() == fold(item["text"])[0].strip()
    if too_short(span) and not whole:
        raise _Bad(f"its span is shorter than {MIN_QUOTE_CHARS} characters: {inline_code(span)}")
    if find_quote(item["text"], span) is None:
        raise _Bad(f"its span is not word for word in the item's text: {inline_code(span)}")
    return span


def _related(repo: KbRepo, live: set[str], kind: str, value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        raise _Bad("`related` must be a list of record IDs")
    terms = kind in _ENTITY_GAPS
    bad = [r for r in value if r not in live or (terms and not isinstance(repo.records[r], Term))]
    if bad:
        raise _Bad(f"`related` names no live {'term' if terms else 'record'}: {', '.join(inline_code(r) for r in bad)}")
    return list(dict.fromkeys(value))


def _check(repo: KbRepo, live: set[str], item: dict, entry: dict) -> dict:
    """The verdict `entry` for `item`, normalised, or _Bad."""
    kind = item["kind"]
    extra = sorted(str(k) for k in entry if k not in _FIELDS[kind])
    if extra:
        raise _Bad(f"fields this item does not take: {', '.join(inline_code(k) for k in extra)}")
    out: dict = {"id": item["id"], "span": _span(item, entry.get("span"))}
    note = entry.get("note") or ""
    if not isinstance(note, str) or len(note) > MAX_NOTE:
        raise _Bad(f"`note` must be text of at most {MAX_NOTE} characters")
    out["note"] = note
    for q in YES_NO.get(kind, ()):
        value = entry.get(q)
        if isinstance(value, bool):  # YAML 1.1 habits: yes/no written unquoted
            value = "yes" if value else "no"
        out[q] = _one_of(q, value, ("yes", "no"))
    if kind in _VERDICT_VALUES:
        out["verdict"] = _one_of("verdict", entry.get("verdict"), _VERDICT_VALUES[kind])
    if kind in _GAPS:
        for field, allowed in (("kind_hint", KIND_HINTS), ("severity", SEVERITIES)):
            value = entry.get(field)
            if value is None and out["verdict"] != "missing":
                continue
            if value is None:
                raise _Bad(f"a missing rule needs `{field}` ({', '.join(allowed)})")
            out[field] = _one_of(field, value, allowed)
    if kind in _GAPS + _ENTITY_GAPS:
        out["related"] = _related(repo, live, kind, entry.get("related"))
        if out["verdict"] == "covered" and not out["related"]:
            raise _Bad("`covered` needs the ID of the record that covers it in `related`")
    if out.get("verdict") == "missing":
        span = out["span"]
        if kind == "entity_missing" and not mentions(span, _names_term(item["names"])):
            raise _Bad(f"its span does not name the entity ({', '.join(inline_code(n) for n in item['names'])}): {inline_code(span)}")
        if kind == "relation_missing":
            a, b = item["ends"]
            if not names_both(span, _names_term(a), _names_term(b)):
                raise _Bad(f"its span does not name both {inline_code(a[0] if a else '')} and {inline_code(b[0] if b else '')} "
                           f"at separate places: {inline_code(span)}")
    return out


def _invalid(message: str) -> Issue:
    return Issue("eval.verdict_invalid", message, "error", VERDICTS)


def _read_verdicts(root: Path) -> tuple[list, list[Issue]]:
    """The entries of VERDICTS ([] when there is no file), or an issue saying why the file is unusable."""
    data = read_regular(Path(root), VERDICTS, max_bytes=MAX_VERDICTS_BYTES)
    if data is None:
        if (Path(root) / VERDICTS).exists():
            return [], [_invalid(f"not a regular file, or larger than {MAX_VERDICTS_BYTES} bytes")]
        return [], []
    try:
        doc = load_yaml_strict(data.decode("utf-8"))
    except (UnicodeDecodeError, YamlError) as e:
        return [], [_invalid(f"does not parse: {inline_code(str(e))}")]
    if not isinstance(doc, dict) or list(doc) != ["verdicts"] or not isinstance(doc["verdicts"], list):
        return [], [_invalid("must be a mapping with one key, `verdicts`, holding a list of verdicts")]
    return doc["verdicts"], []


def check_verdicts(root: Path, repo: KbRepo, items: list[dict]) -> tuple[list[dict], list[Issue]]:
    """The valid verdicts for `items` (already checked against JUDGE_ITEMS), normalised, in file order, and an
    `eval.verdict_invalid` issue per entry set aside (an unknown item id, a second answer to one item, a span not in
    the item's text, a value not allowed, an id that is not live) and an `eval.verdict_missing` per unanswered item."""
    by_id = {i["id"]: i for i in items}
    live = live_ids(repo)
    entries, issues = _read_verdicts(root)
    answered: set[str] = set()
    out: list[dict] = []
    for n, entry in enumerate(entries, 1):
        iid = entry.get("id") if isinstance(entry, dict) else None
        where = inline_code(iid) if isinstance(iid, str) else f"entry {n}"
        try:
            if not isinstance(entry, dict):
                raise _Bad("not a mapping")
            if not isinstance(iid, str):
                raise _Bad("has no `id`")
            if iid not in by_id:
                raise _Bad("not an item of the judge brief")
            if iid in answered:
                raise _Bad("answered twice; only the first answer counts")
            answered.add(iid)
            out.append(_check(repo, live, by_id[iid], entry))
        except _Bad as e:
            issues.append(_invalid(f"{where}: {e}"))
    issues += [Issue("eval.verdict_missing", f"{inline_code(i['id'])}: no verdict", "error", VERDICTS)
               for i in items if i["id"] not in answered]
    return out, issues


def check_items_file(root: Path, items: list[dict]) -> None:
    """Raise EvalError unless JUDGE_ITEMS holds exactly `items` (what kb asks about now)."""
    data = read_regular(Path(root), JUDGE_ITEMS, max_bytes=MAX_ITEMS_BYTES)
    if data is None:
        raise EvalError(f"no judge items at {JUDGE_ITEMS}: run `kb eval judge` first")
    try:
        written = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        written = None
    if written != json.loads(items_json(items)):
        raise EvalError(f"{JUDGE_ITEMS} is not the item list kb asks about now (it was edited, or the knowledge base or "
                        "run B changed since): run `kb eval judge` again")


def validate_verdicts(root: Path, repo: KbRepo) -> tuple[list[dict], list[Issue]]:
    """Recompute the items, check JUDGE_ITEMS against them (EvalError on a mismatch), then check VERDICTS."""
    items = judge_state(root, repo).items
    check_items_file(root, items)
    return check_verdicts(root, repo, items)
