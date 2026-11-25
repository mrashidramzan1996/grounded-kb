# Extraction Evaluation Layer (0.4.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "did the agent extract every requirement, and is each one right?" measurable and decidable. Deterministic sentence-level checks come first. A blind second extraction is aligned by quote span, an evaluation agent judges one item at a time, and the person decides every disagreement, with the decisions committed in git.

**Architecture:** kb stays LLM-free. It splits stored segments into sentences, tags rule-like ("normative") sentences, and computes coverage exactly from quote spans. The LLM work is done by the user's coding agent from two kb briefs: a blind extraction into `.kb/cache/eval/run-b/` and a judge brief answered in `.kb/cache/eval/verdicts.yaml`. kb aligns, validates and scores both deterministically, and turns what remains into `kb todo` ask items. Human decisions go into `kb/eval/decisions.yaml` through `kb eval decide`, and those decisions suppress the matching findings from then on. Requirements gain four structured fields, a new `EXCL` record type holds scope exclusions, and the 0.3.1 CI limit is fixed by splitting the workflow into a policy job and a test job.

**Tech Stack:** Python ≥3.12, uv, Typer, pydantic v2, pytest. Code map: `/private/tmp/claude-501/-Users-dev-projects-kb/7309a439-0cc8-4296-8435-18da0f1acd39/scratchpad/codemap-040.md` (file:line references for everything below).

**Spec:** No separate spec. The authority is the research report `reports/Ground truth extraction evaluation.md` (its "Recommended architecture" section) and the design the user approved in this session: extract twice, align into unique items, have an evaluation agent verify every truth is correct and none is missing, back every proposal with code verifiers, let the person decide, and add classification fields plus a scope-exclusion type.

## Global Constraints

- kb makes no model calls and needs no API key; all LLM work happens through briefs the user's agent follows.
- Everything an LLM proposes is verified by code before a person sees it: each span must exist verbatim in the cited segment, IDs must exist, and numbers must appear in quotes.
- The evaluation agent proposes and never approves. Only a person's answer, recorded with `kb eval decide --by <handle>` or `kb clarify`, resolves a finding.
- Approved output is never used as ground truth for scoring. The QC sample is shown without the agent's verdict.
- Backward compatible: a 0.3.x repo still passes `kb check` with warnings only. New rules are errors only for records drafted in the current ingest or redraft range (the `mine` set of the ingest and redraft reports).
- Untrusted text (documents, records, verdict files) goes through `inline_code` / `_fence` / `_md` exactly as today, and never goes into a printed command.
- `.kb/cache/` is agent-writable, so kb re-validates everything it reads from there against the repo, as `redraft_report._check_files` already does.
- Exit codes: 0 ok, 1 gate or policy failure, 2 usage, config or tool error.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01FU26Rg1DsNpsAtdAuPuQzD`.

## Review Focus

1. Abbreviations and decimals ("e.g.", "v3.1", "5.5 EUR", "U.S.") must not split sentences, and bullet lists and table rows must split into one unit per item. (Task 1: `test_abbreviations_and_decimals_do_not_split`, `test_list_items_and_table_rows_are_units`.)
2. A quote that starts or ends in the middle of a word ("2" inside "25", "seat" inside "seats") must not count as found, but a quote ending in punctuation must still match. (Task 2: `test_quote_must_align_to_word_and_number_boundaries`.)
3. A segment that is only a heading, or has no rule-like sentence, must produce no coverage warning and must not need a triage reason beyond the existing `none:`. (Task 3: `test_heading_only_segment_is_silent`.)
4. A blind run that uses the same IDs as live records, or quotes a segment that does not exist, must be rejected item by item. It must never crash or get merged into `kb/`. (Task 6: `test_run_b_items_are_validated_not_trusted`.)
5. A verdicts file that answers items not in the judge brief, skips items, or gives a span not in the segment must have those entries rejected. The remaining verdicts are still used. (Task 7: `test_verdicts_outside_the_brief_are_rejected`.)

---

### Task 1: Sentence units and rule-like (normative) cues

**Files:**
- Create: `src/kb/sources/sentences.py`
- Test: `tests/sources/test_sentences.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Sentence: ref: str` ("SRC-x#seg-NN"), `index: int` (0-based within the segment), `start: int`, `end: int` (offsets into the source's stored text.md), `text: str`, `cues: frozenset[str]`.
  - `def sentences(repo: KbRepo, src_id: str) -> list[Sentence]`, ordered by segment then index.
  - `def segment_sentences(text: str, seg: Segment, src_id: str) -> list[Sentence]`.
  - `def cues(text: str) -> frozenset[str]`.
  - `def is_normative(s: Sentence) -> bool` (equivalent to `bool(s.cues)`).
  - `def sentence_key(s: Sentence) -> str`: sha256 of the folded sentence text, first 12 hex characters. It is stable when only other sentences change.
  - `NORMATIVE_CUES: dict[str, re.Pattern]` with exactly these keys:
    - `"must"`: must / shall / required / requires / is required to / has to / needs to
    - `"must_not"`: must not / shall not / may not / cannot / can not / never / prohibited / not allowed / no longer
    - `"should"`: should / recommended
    - `"may"`: \bmay\b not followed by "not"
    - `"limit"`: at least / at most / no more than / no less than / up to / within / maximum / minimum / only
    - `"number"`: a digit run followed by an optional unit or %
  - Cue patterns are case-insensitive and match on word boundaries.
- Consumes: `Segment` (sources/segment.py:15), `KbRepo.segments`, `KbRepo.source_text`, `fold` (quotes/match.py).

- [ ] **Step 1: Write the failing tests.**
  - `test_sentences_cover_segment_text_in_order`: for every Sentence, `text_md[s.start:s.end] == s.text`, the spans don't overlap, and they lie inside the segment.
  - `test_heading_line_is_not_a_sentence`: a segment opening with a `#`-prefixed heading line yields no Sentence for that line.
  - `test_abbreviations_and_decimals_do_not_split`: "Use e.g. the 5.5 EUR plan in v3.1. Then stop." yields 2 sentences.
  - `test_list_items_and_table_rows_are_units`: "- a must x\n- b must y" yields 2 units, and a table-row segment yields 1.
  - `test_cues`:
    - `cues("Refunds must be paid within 14 days.") == {"must", "limit", "number"}`
    - `cues("Seats cannot be removed.") == {"must_not"}`
    - `cues("Users may export data.") == {"may"}`
    - `cues("The product helps teams.") == set()`
  - `test_sentence_key_stable_when_neighbours_change`.
- [ ] **Step 2: Run them and confirm they fail.** Run `uv run pytest tests/sources/test_sentences.py -q`; expect an ImportError.
- [ ] **Step 3: Implement.**
  - Split on `(?<=[.!?])\s+(?=[A-Z0-9"(])`, protecting these abbreviations: e.g., i.e., etc., vs., U.S., No., Fig., approx., incl., resp.
  - Do not split inside a number (`\d\.\d`).
  - Each line starting with `- `, `* `, `+ `, `\d+[.)] ` or `| ` starts a new unit.
  - Skip blank and heading-only lines.
- [ ] **Step 4: Run the tests and confirm they pass.** Then run `uv run ruff check src/kb/sources/sentences.py`.
- [ ] **Step 5: Commit** with the message `feat(sources): sentence units and rule-like cues`.

### Task 2: Quote boundaries, minimum length, number check, `evidence.mode`

**Files:**
- Modify: `src/kb/quotes/match.py` (`find_quote`), `src/kb/gates/quotes.py`, `src/kb/ingest/report.py` and `src/kb/ingest/redraft_report.py` (escalation of drafted-record codes), `src/kb/init/templates/kb_readme.md`
- Test: `tests/quotes/test_match.py`, `tests/gates/test_quote_gate.py`, `tests/ingest/test_report.py`

**Interfaces:**
- Produces:
  - `find_quote(haystack, quote)` keeps its signature. It returns only matches where the folded match does not begin right after an alphanumeric character when the quote begins with one, and does not end right before an alphanumeric character when the quote ends with one. It keeps searching later occurrences.
  - `MIN_QUOTE_CHARS = 12`: a quote shorter than this, counting non-space characters, raises issue `quote.too_short` (error).
  - `_ID_LIKE` narrowed to kb's own IDs only (`REQ-F-`, `REQ-NF-`, `FEAT-`, `ADR-`, `INV-`, `EXCL-`, `COMP-`, `TERM-`, `seg-`). "ISO-27001" now counts as a number.
  - `statement.number_not_in_quote` stays a warning in the gate. The ingest and redraft reports escalate it to an error for records in their `mine` set that have `drafted_by` set, using the mechanism that already re-issues `evidence.missing`.
  - `evidence.mode`:
    - `"all"` (the default): every quote must be found. This is the current behaviour.
    - `"any"`: an unfound quote is a warning `quote.not_found` as long as at least one quote is found; if none is found it is an error.
- [ ] **Step 1: Write the failing tests.**
  - `test_quote_must_align_to_word_and_number_boundaries`:
    - `find_quote("Minimum seats: 25", "Minimum seats: 2") is None`
    - `find_quote("two seats", "seat") is None`
    - `find_quote("Pay within 14 days.", "within 14 days.")` is found
    - `find_quote("a 2 and a 25", "a 2")` matches the first occurrence
  - `test_short_quote_rejected`: a quote "s" gives `quote.too_short`.
  - `test_iso_number_checked`: statement "Comply with ISO-27001.", quote "Comply with ISO-9001." gives `statement.number_not_in_quote`.
  - `test_drafted_number_mismatch_is_rejected_in_report`: through `ingest_report`, the record is rejected.
  - `test_mode_any_allows_one_unfound_quote` and `test_mode_any_none_found_is_error`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement as specified.**
- [ ] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/quotes tests/gates tests/ingest -q`. Update any existing fixture quotes that now fail the boundary rule: fix the fixture, not the rule.
- [ ] **Step 5: Update kb_readme.md** ("Record formats": quote boundaries, minimum length, `mode`).
- [ ] **Step 6: Commit** with the message `fix(quotes): word/number boundaries, minimum length, ISO-style numbers, evidence.mode any`.

### Task 3: Coverage gate (sentence level), merge flag, triage integrity

**Files:**
- Create: `src/kb/gates/coverage.py`
- Modify: `src/kb/gates/triage.py`, `src/kb/records/models.py` (`Triage` validator), `src/kb/check.py:20` (register), `src/kb/ingest/report.py:30` (register `coverage_gate` and `triage_gate`), `src/kb/verify/approvals.py` (route triage changes), `src/kb/init/scaffold.py:22`, `src/kb/init/templates/kb_readme.md`
- Test: `tests/gates/test_coverage.py`, `tests/gates/test_triage_gate.py`, `tests/verify/test_approvals.py`

**Interfaces:**
- Consumes: Task 1 `sentences`, `is_normative`, `sentence_key`; Task 2 `find_quote`.
- Produces:
  - `def quote_spans(repo: KbRepo) -> dict[str, list[tuple[int, int, str]]]`: maps each source id to `(start, end, record_id)` text.md offsets for every quote of a live Requirement, Decision, Invariant or Exclusion (Task 5 adds Exclusion; until then the three existing types).
  - `def uncovered(repo: KbRepo, decided: set[str] = frozenset()) -> list[Sentence]`: the normative sentences that no quote span overlaps, excluding sentences whose `sentence_key` is in `decided`. Task 8 passes the keys from decisions.yaml.
  - `coverage_gate(ctx) -> list[Issue]` emits these issues:
    - `sentence.uncovered` (warning, path `kb/sources/<src>/triage.yaml`, record `<src>`): ``"{ref}: rule-like sentence no record quotes: {inline_code(text)}"``.
    - `quote.multiple_rules` (warning, record = record id): ``"{rid}: its quote spans {n} rule-like sentences; split it into one record per rule"``. Raised when a single quote overlaps two or more normative sentences.
    - `triage.none_has_rules` (warning): a segment triaged `none:` contains a normative sentence.
  - `def coverage_stats(repo) -> dict`: `{"normative": int, "covered": int, "ratio": float, "quoted_chars": int, "text_chars": int}`. The quoted share is reported next to coverage so that coverage cannot be gamed by quoting whole segments.
  - Triage integrity:
    - `triage.not_quoted` (error): a triage list for `seg` names no record whose quotes, or whose linked requirement's quotes for a Feature, claim or Term, cite that `SRC#seg`. Message: ``"{src}#{seg}: triage names {ids} but none of them quotes this segment"``.
    - `none:` reasons must start with a code from `NONE_CODES = ("heading", "background", "contact", "boilerplate", "example", "duplicate", "out-of-scope", "other")`, giving the form `none: <code>[: free text]`. A `none:` value with no known code gives `triage.none_uncoded` (warning; error in the ingest and redraft reports for triage files in `mine`). The code `out-of-scope` gives `triage.use_exclusion` (warning: "record it as an EXCL record instead"). Task 5 adds EXCL.
  - Approvals: a changed `kb/sources/<SRC>/triage.yaml` requires the intent approvers, with reason ``"triage of {src} changed"``. Add `/kb/sources/*/triage.yaml` to `_APPROVAL_PATHS` on the intent line.
- [ ] **Step 1: Write the failing tests.**
  - `test_uncovered_rule_sentence_warns`: a segment with two "must" sentences, one of them quoted, gives exactly one `sentence.uncovered` naming the other.
  - `test_heading_only_segment_is_silent`.
  - `test_quote_spanning_two_rules_flags_merge`.
  - `test_triage_none_with_rule_warns`.
  - `test_triage_naming_non_quoting_record_is_error`: `seg-07: [FEAT-001]`, where FEAT-001's requirements don't quote seg-07, gives `triage.not_quoted`; a record that does quote the segment gives no issue.
  - `test_none_reason_codes`: `none: heading` passes, `none: whatever` gives `triage.none_uncoded`, `none: out-of-scope: v2` gives `triage.use_exclusion`.
  - `test_coverage_stats`.
  - `test_triage_change_needs_intent_approvers`.
  - `test_ingest_report_rejects_new_uncoded_none`: a triage file drafted in range with an uncoded `none` is rejected.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Overlap means `max(starts) < min(ends)` on text.md offsets. Quote spans are found with `find_quote` inside the cited segment's slice and then offset by `seg.start`.
- [ ] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/gates tests/ingest tests/verify tests/init -q`. Update the acme draft fixtures (`tests/fixtures/drafts/acme-billing`) and kb_valid triage `none:` values so they carry codes.
- [ ] **Step 5: Update kb_readme.md** ("Triage" section: codes, the integrity rule, approvers).
- [ ] **Step 6: Commit** with the message `feat(gates): sentence-level coverage, merge flag, triage integrity and reason codes`.

### Task 4: Requirement classification fields

**Files:**
- Modify: `src/kb/records/models.py:42` (Requirement), `src/kb/gates/quotes.py` (field checks), `src/kb/ingest/templates/brief.md` and `src/kb/ingest/brief.py` (`_example_records`, rules), `src/kb/ingest/templates/redraft*.md`, `src/kb/ingest/report.py` and `redraft_report.py` (escalation), `src/kb/context.py` (show fields), `src/kb/init/templates/kb_readme.md`
- Test: `tests/records/test_models.py`, `tests/gates/test_requirement_fields.py`, `tests/ingest/test_brief.py`

**Interfaces:**
- Produces these optional fields on `Requirement`:
  - `modality: Literal["must", "must_not", "should", "may"] | None = None`
  - `actor: str | None = None`: who the rule binds, at most 60 characters, plain text with no markup.
  - `category: Literal["performance", "security", "privacy", "availability", "reliability", "usability", "compliance", "compatibility", "maintainability", "portability"] | None = None`. Only allowed on `REQ-NF-*`; setting it on `REQ-F-*` is a validation error.
  - `origin: Literal["stated", "implied"] = "stated"`.
- Issue codes (all warnings in the gate; Step 3 says which become errors for drafted records in reports):
  - `requirement.fields_missing`: modality or actor is missing, or category is missing on a REQ-NF.
  - `requirement.modality_mismatch`: the quote's cues contain a modality cue that differs from `modality`. Map the cue keys from Task 1: `must`→must, `must_not`→must_not, `should`→should, `may`→may. When the quote has several, any match passes.
  - `requirement.no_rule_word`: `origin: stated` but the quote has no modality cue. The suggested fix is "if the document implies it, set origin: implied".
  - `requirement.implied_unconfirmed`: `origin: implied` and no quote cites `SRC-clarifications`. Task 8 turns this into an ask.
- [ ] **Step 1: Write the failing tests.**
  - The model accepts and rejects as specified (category on REQ-F is rejected).
  - `test_fields_missing_is_warning_in_check_error_in_report`.
  - `test_modality_mismatch`: quote "must", modality "should" gives the warning; quote "must … or may …", modality may passes.
  - `test_stated_without_rule_word`.
  - `test_implied_needs_clarification`.
  - `test_brief_examples_show_fields`: the rendered brief's example requirement includes all four fields, and the rules section explains them.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** The reports escalate `requirement.fields_missing` and `requirement.modality_mismatch` to errors for drafted records in `mine`. `no_rule_word` and `implied_unconfirmed` stay warnings. Brief rule text, added as rule 3b: "Give every requirement `modality` (must, must_not, should, may), `actor` (who it binds), `category` for REQ-NF, and `origin` (stated, or implied when the document implies it without saying it). An implied requirement must later be confirmed by the user."
- [ ] **Step 4: Run** `uv run pytest tests/records tests/gates tests/ingest tests/test_context.py -q`. Update the acme draft fixtures with the fields.
- [ ] **Step 5: Commit** with the message `feat(records): requirement modality, actor, category and origin`.

### Task 5: Scope-exclusion record type (`EXCL`)

**Files:** every place on the code map's "add a new record type" list (§4 items 1–13): `records/ids.py`, `records/models.py`, `records/io.py`, `records/repo.py:19`, `gates/ids.py`, `gates/quotes.py`, `ingest/brief.py` (`_NUMBERED`, examples), `ingest/templates/brief.md`, `ingest/report.py` (citers), `ingest/redraft.py` and `redraft_report.py` (`_SELECTABLE`), `verify/approvals.py` (`_INTENT_DIRS` and the reason chain), `init/scaffold.py:22`, `context.py` (`_Index`, `ContextPack`, `_md`, `_json`, `_summary`), `trace.py`, `init/templates/kb_readme.md`
- Test: `tests/records/test_exclusion.py`, plus additions to the existing context, trace, approvals and brief tests

**Interfaces:**
- Produces:
  - `EXCL_ID = r"EXCL-\d{3,}"`, `RecordType.EXCLUSION`, and files at `kb/exclusions/EXCL-001.yaml`.
  - `class Exclusion(_Record): id, statement: str, evidence: Evidence (required), modules: list[str] = [], features: list[str] = []`.
- Behaviour:
  - It is quote-checked like a Requirement.
  - It is served by `kb context` in a section headed `## Out of scope (live)` (JSON key `exclusions`), selected by module or feature exactly as requirements are.
  - Its changes need the intent approvers (`"exclusion {rid} changed"`).
  - It is redraftable.
  - It counts as covering its sentences in Task 3's `quote_spans`.
- [ ] **Step 1: Write the failing tests.**
  - Parse and render round-trip.
  - `parse_id("EXCL-001")`.
  - A missing quote gives `quote.not_found`.
  - `kb context --module MOD-a` lists it under "Out of scope".
  - `kb trace EXCL-001` shows its quote.
  - Its change needs intent approvers.
  - The brief's next-ID list includes `EXCL`.
  - The redraft accepts `EXCL-001`.
  - A quoted out-of-scope sentence is no longer `sentence.uncovered`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Replace the `assert isinstance(rec, Term)` endings in `context._summary` and `trace.py` with explicit Exclusion handling, ending in a clear error for unknown types rather than an AssertionError.
- [ ] **Step 4: Run** `uv run pytest tests/records tests/gates tests/ingest tests/verify tests/test_context.py tests/test_trace.py -q`.
- [ ] **Step 5: Commit** with the message `feat(records): EXCL scope-exclusion records`.

### Task 6: Blind second extraction and deterministic alignment

**Files:**
- Create: `src/kb/eval/__init__.py`, `src/kb/eval/extract.py`, `src/kb/eval/align.py`, `src/kb/eval/templates/extract.md`
- Modify: `src/kb/cli.py` (new `eval_app` group), `pyproject.toml` package data if templates need it (follow how `kb.ingest` ships templates)
- Test: `tests/eval/test_extract.py`, `tests/eval/test_align.py`

**Interfaces:**
- Consumes: `build_brief` pieces (`_segments`, `_example_records`, rules) from ingest/brief.py; Task 3 `quote_spans`; `find_quote`.
- Produces:
  - Constants: `EVAL_DIR = ".kb/cache/eval"`, `RUN_B = ".kb/cache/eval/run-b"`, `EXTRACT_BRIEF = ".kb/cache/eval/extract.md"`, `EVAL_BASE = ".kb/cache/eval/base"`.
  - `def build_extract_brief(root: Path, config: KbConfig) -> str`.
    - Content: the ingest rules plus every segment of every source. Records go to `.kb/cache/eval/run-b/<same layout as kb/>`, with IDs prefixed `B-` (`B-REQ-F-001`, …).
    - Header: "Work in a fresh session or subagent. Do not read kb/requirements, kb/decisions, kb/invariants, kb/exclusions or kb/features; extract from the segments below only. Work segment by segment, bottom-up."
    - `drafted_by` must say `via kb eval extract`.
  - `kb eval extract` writes the brief and `EVAL_BASE` (HEAD), clears `RUN_B`, and prints the path.
  - `@dataclass(frozen=True) class Item: id: str, kind: str ("requirement"|"decision"|"invariant"|"exclusion"), statement: str, spans: tuple[tuple[str, int, int], ...]`. Each span is `(src, start, end)` in text.md offsets.
  - `def load_run_b(root: Path, repo: KbRepo) -> tuple[list[Item], list[Issue]]`. It parses run-b files with the record parser after stripping the `B-` prefix for schema purposes. It validates every item and keeps the good ones. Each bad item gets an issue `eval.run_b_invalid` naming the file: unknown source or segment, quote not found under Task 2's rules, a numeric field not in its quote, or an ID without the `B-` prefix.
  - `@dataclass(frozen=True) class Alignment: matched: list[tuple[str, str]]` (kb id, B id); `only_kb: list[str]`, `only_b: list[Item]`, `splits: list[tuple[str, list[str]]]` (one kb record against several B items), `merges: list[tuple[list[str], str]]`.
  - `def align(kb_items: list[Item], b_items: list[Item]) -> Alignment`. Two items match when, for some shared source, `overlap / min(len_a, len_b) >= 0.5`. Use many-to-one matching. A kb item matched by 2 or more B items is a split, and a B item matching 2 or more kb items is a merge.
  - `def kb_items(repo: KbRepo) -> list[Item]`: live records with evidence.
  - `def agreement(al: Alignment, n_kb: int, n_b: int) -> dict`: `{"matched": m, "only_kb": .., "only_b": .., "span_f1": 2m/(n_kb+n_b), "estimated_total": round(n_kb*n_b/m) if m else None}`. The capture–recapture estimate is labelled "upper bound on recall" wherever it is shown.
- [ ] **Step 1: Write the failing tests.**
  - `test_extract_brief_is_blind_and_complete`: the brief contains every segment ref and the blind header, and contains no live record statement.
  - `test_run_b_items_are_validated_not_trusted`: a B file with a bad segment, an unfound quote and an ID without `B-` gives three issues, while the good items load.
  - `test_align_matches_by_quote_overlap`.
  - `test_align_detects_split_and_merge`.
  - `test_agreement_numbers`: 16 kb, 18 B, 14 matched gives span_f1 0.8235… and estimated_total 21.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** `kb eval extract` exits 2 when nothing is ingested.
- [ ] **Step 4: Run** `uv run pytest tests/eval -q`.
- [ ] **Step 5: Commit** with the message `feat(eval): blind second extraction and quote-span alignment`.

### Task 7: Judge brief, verdict validation, findings

**Files:**
- Create: `src/kb/eval/judge.py`, `src/kb/eval/findings.py`, `src/kb/eval/templates/judge.md`
- Modify: `src/kb/cli.py` (`kb eval judge`, `kb eval report`), `src/kb/records/repo.py` (treat `kb/eval/summary.yaml` and `kb/eval/decisions.yaml` as known non-record files, not `schema.unknown_file`)
- Test: `tests/eval/test_judge.py`, `tests/eval/test_findings.py`

**Interfaces:**
- Consumes: Task 3 `uncovered`, `coverage_stats`; Task 4 fields; Task 6 `load_run_b`, `align`, `kb_items`, `agreement`.
- Produces:
  - `JUDGE_BRIEF = ".kb/cache/eval/judge.md"`, `VERDICTS = ".kb/cache/eval/verdicts.yaml"`, `JUDGE_ITEMS = ".kb/cache/eval/items.json"` (the item list kb asked about, written by kb).
  - `def judge_items(repo, alignment, decided: set[str]) -> list[dict]`. Each item has `id`, `kind`, `ref`, `text` and `context`, with IDs and kinds as follows:
    - `R:<rid>` (kind `faithful`): every live requirement or exclusion.
    - `S:<sentence_key>` (kind `uncovered`): every `uncovered()` sentence.
    - `B:<b-id>` (kind `only_b`): every `only_b` item.
    - `M:<rid>` (kind `split_merge`): every split and merge.
    - The sort order is stable.
  - `kb eval judge` checks that `EVAL_BASE` equals HEAD, then writes `JUDGE_ITEMS` and the brief.
    - The brief rules: "Judge each item on its own; you have not seen any other verdicts. Over-flag: if unsure whether a sentence is a rule, say `missing`. Every verdict needs a verbatim `span` copied from the item's text."
    - The per-kind questions:
      - **faithful:** yes/no each for `same_meaning`, `same_actor`, `same_modality` and `no_added_constraint`, plus a note.
      - **uncovered / only_b:** `verdict` in {missing, covered, not_a_rule, unknown}, with `kind_hint` in {requirement, decision, invariant, exclusion} when missing, `severity` in {high, medium, low}, `related` (existing record IDs) and `span`.
      - **split_merge:** `verdict` in {ok, should_split, should_merge, unknown}.
  - `def validate_verdicts(root, repo) -> tuple[list[dict], list[Issue]]`. It re-reads `JUDGE_ITEMS` and checks it equals `judge_items(...)` recomputed now; on a mismatch it raises `EvalError` with the hint "run `kb eval judge` again".
    - Verdicts are rejected (issue `eval.verdict_invalid`) for an unknown item ID, a span not found in the item text (Task 2 rules), an unknown enum value, or `related` IDs that don't exist.
    - A missing verdict gives `eval.verdict_missing`.
  - `@dataclass(frozen=True) class Finding: id: str` (same as item id), `kind: str` (`omission`, `unfaithful`, `split`, `merge`, `unknown`), `ref: str`, `text: str`, `severity: str`, `supporters: int` (how many of {kb coverage gate, run B, judge} flagged it), `related: tuple[str, ...]`, `note: str`.
  - `def build_findings(items, verdicts, alignment) -> list[Finding]`:
    - `omission`: an S or B item with verdict missing or unknown. An S item that run B also extracted gets supporters = 3.
    - `unfaithful`: an R item with any `no`.
    - `split` / `merge`: from split_merge items when they are not `ok`.
    - Ranking: high severity before medium before low, then supporters descending, then ID.
  - `def fingerprint(repo) -> str`: sha256 over sorted (live record id, raw bytes) plus every source's segment hashes, first 16 hex characters; files under `kb/eval/` are ignored.
  - `kb eval report [--write]`:
    - It prints Markdown: coverage stats, agreement (with "upper bound" wording), findings grouped by kind with escaped text, and rejected verdicts.
    - `--write` writes committed `kb/eval/summary.yaml` with: `commit` (EVAL_BASE), `fingerprint` (Task 8), `coverage` and `agreement` dicts, `open_findings` (list of Finding as dicts), `judged_at` (ISO date).
    - It exits 1 when any verdict is missing or invalid.
- [ ] **Step 1: Write the failing tests.**
  - `test_judge_items_cover_every_record_and_gap`.
  - `test_verdicts_outside_the_brief_are_rejected`: an unknown ID, a span not in the text, or a bad enum each give `eval.verdict_invalid`, while the other verdicts are kept.
  - `test_tampered_items_file_is_refused`.
  - `test_findings_ranked`.
  - `test_unfaithful_from_any_no`.
  - `test_report_write_produces_summary`, with a round-trip through YAML.
  - `test_eval_files_are_not_unknown_records`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** `EvalError(ValueError)` is added to the CLI's exit-2 errors.
- [ ] **Step 4: Run** `uv run pytest tests/eval -q`.
- [ ] **Step 5: Commit** with the message `feat(eval): judge brief, verdict validation and ranked findings`.

### Task 8: Decisions, `kb todo` integration, QC sample, approvals

**Files:**
- Create: `src/kb/eval/decisions.py`, `src/kb/eval/sample.py`
- Modify: `src/kb/todo.py`, `src/kb/cli.py` (`kb eval decide`, `kb eval sample`, `kb eval status`), `src/kb/gates/coverage.py` (honour decisions), `src/kb/verify/approvals.py` (route `kb/eval/`), `src/kb/init/scaffold.py:22`
- Test: `tests/eval/test_decisions.py`, `tests/eval/test_sample.py`, `tests/test_todo_eval.py`

**Interfaces:**
- Consumes: Task 7 `Finding`, `fingerprint`, `kb/eval/summary.yaml`; Task 3 `uncovered`; Task 4 `requirement.implied_unconfirmed`.
- Produces:
  - `DECISIONS = "kb/eval/decisions.yaml"` with schema `decisions: list[Decision]`. Each entry has:
    - `finding: str`
    - `sentence_key: str | None`
    - `decision: Literal["added", "covered", "not_a_rule", "product_decision", "correct", "fixed", "ok"]`
    - `record: str | None`
    - `reason: str | None` (at most 200 characters, plain)
    - `by: str` (a handle, validated like `kb clarify --by`)
    - `date: str` (ISO)
  - `def decided_keys(repo_root) -> set[str]`: the sentence keys whose decision is added, covered or not_a_rule. These are passed to `uncovered()`, so decided sentences stop warning.
  - `kb eval decide <finding-id> --as <decision> --by <handle> [--record ID] [--reason TEXT]` validates the decision:
    - `added`: the record exists, is live, and quotes the finding's sentence (span overlap).
    - `covered`: the record exists and is live.
    - `fixed`: the record was modified since `summary.commit`.
    - Every decision: the finding is in summary.yaml's open_findings.
    - On success it appends to decisions.yaml atomically. A failure exits 2 and writes nothing.
  - `kb todo` additions:
    - Do item `eval:extract` (`kb eval extract`) when live records exist and summary.yaml is missing or its fingerprint ≠ the current one. The fingerprint is computed ignoring `kb/eval/` itself.
    - Do item `eval:run-b` (no command; message "follow .kb/cache/eval/extract.md in a fresh session or subagent, then run `kb eval judge`") when `EXTRACT_BRIEF` exists for HEAD and RUN_B is empty.
    - Do item `eval:judge` (`kb eval judge`) once run-b has files.
    - Do item `eval:verdicts` (no command: "follow .kb/cache/eval/judge.md, then run `kb eval report --write`") when `JUDGE_ITEMS` exists and summary is stale.
    - Ask items, one per open finding not yet in decisions, all **blocking** (they make `clean` False and block the freeze ask). They are rendered with `_question` and have these options:
      - **omission:** "(a) Add it as a new record" then "write a record quoting this sentence, run `kb ingest --report`, then `kb eval decide <id> --as added --record <new id> --by <handle>`"; "(b) Already covered by a record" then "`kb eval decide <id> --as covered --record <id> --by <handle>`"; "(c) Not a rule" then "`kb eval decide <id> --as not_a_rule --reason <why> --by <handle>`"; "(d) Needs a product decision" then "record the user's answer with `kb clarify`, draft from it, then decide `--as added`".
      - **unfaithful:** "(a) Fix the record" then "`kb redraft <rid>`, follow the brief, `kb redraft --report`, then decide `--as fixed`"; "(b) It is correct" then decide `--as correct`.
      - **split / merge:** "(a) Split or merge as suggested" then redraft and decide `--as fixed`; "(b) Keep as is" then decide `--as ok`.
    - Ask item `implied:<rid>` for each `requirement.implied_unconfirmed`, with options "(a) Confirm" then "`kb clarify` with the user's confirmation, add it as a quote, `kb redraft <rid>`" and "(b) Not a requirement" then retire it. This ask is blocking.
    - One optional ask, `categories`, when no live REQ-NF has category security, privacy or availability: "None of the documents state {missing} requirements. Is that right?" with options "(a) Correct, none apply" then decide `--as not_a_rule` on finding `categories` (kb accepts that pseudo-finding), and "(b) I will give them" then `kb clarify` and draft.
  - `kb eval sample [--n 10]`: a deterministic QC sample seeded by HEAD sha. It picks n random live records, each with its quote and no verdicts, and n sentences from the unquoted pile. Half the sentences come from normative and half from non-normative, chosen by a seeded `random.Random(int(head[:12], 16))`. It prints a Markdown checklist meant for the PR description.
  - `kb eval status`: prints the summary (coverage, agreement, open and decided counts, whether it is stale).
  - Approvals: `kb/eval/` changes need the intent approvers (`"evaluation decisions changed"`). Add `/kb/eval/` to `_APPROVAL_PATHS`.
- [ ] **Step 1: Write the failing tests.**
  - `test_decide_added_requires_quoting_record`.
  - `test_decide_unknown_finding_exits_2`.
  - `test_decided_sentence_stops_warning`.
  - `test_todo_eval_sequence`: extract, then run-b, then judge, then verdicts, then asks, then clean once all are decided.
  - `test_open_findings_block_freeze`.
  - `test_implied_requirement_ask`.
  - `test_categories_ask_optional`.
  - `test_sample_is_deterministic_and_has_no_verdicts`.
  - `test_eval_dir_changes_need_intent_approvers`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** All ask items use the existing AskItem and Option types, and handles in `then` text are templates like `<handle>`, never filled with untrusted text.
- [ ] **Step 4: Run** `uv run pytest tests/eval tests/test_todo*.py tests/verify tests/records -q`.
- [ ] **Step 5: Commit** with the message `feat(eval): decisions, todo integration, QC sample`.

### Task 9: CI split (policy job and test job) and the 0.3.1 leftovers

**Files:**
- Modify: `src/kb/verify/run.py`, `src/kb/cli.py` (`kb verify --skip-tests`), `src/kb/init/templates/github_workflow.yml`, `src/kb/gitfiles.py` (`_default_branch_ref`), `src/kb/verify/approvals.py` (`policy_files_changed`), README.md
- Test: `tests/cli/test_verify.py`, `tests/init/test_init.py`, `tests/verify/test_policy.py`

**Interfaces:**
- Produces:
  - `kb verify --skip-tests`: approvals, schema, stale-context and policy checks run, and no project test runs. Claims that need tests get status `unverified` with the reason "tests not run (--skip-tests)". This never counts as broken.
  - The workflow template gets two jobs:
    - `kb-policy`: checkout with `fetch-depth: 0`, no dependency install, `uvx --from "$KB_INSTALL_SPEC" kb verify --base "origin/$BASE_REF" --skip-tests --comment --forge auto`. This is the job to mark required.
    - `kb-claims`: installs project dependencies, then runs `kb verify --base "origin/$BASE_REF"` with no `--comment`.
    - A comment explains that the approval gate never runs pull-request code.
  - `_default_branch_ref` reads `git symbolic-ref refs/remotes/origin/HEAD` without `--short` and strips `refs/remotes/origin/`. A tag at `refs/tags/origin/<name>` counts as shadowing (exit 2).
  - `policy_files_changed`: a working-tree policy file that exists but can't be read (too large or not a regular file) counts as changed.
- [ ] **Step 1: Write the failing tests.**
  - `test_skip_tests_runs_no_tests_and_checks_approvals`.
  - `test_workflow_has_policy_job_without_install`: the parsed YAML's `kb-policy` job has no step containing "install", and its run line has `--skip-tests`.
  - `test_default_branch_tag_shadow_refused`.
  - `test_unreadable_policy_file_counts_as_changed`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Update the README "Approvals" prerequisites: make `kb-policy` the required check.
- [ ] **Step 4: Run** `uv run pytest tests/cli tests/init tests/verify -q`.
- [ ] **Step 5: Commit** with the message `fix(ci): approval gate in a job that never runs PR code; 0.3.1 leftovers`.

### Task 10: Playbook, skill, README, e2e, version 0.4.0

**Files:**
- Modify: `src/kb/rules.py` (PLAYBOOK), `src/kb/init/templates/skill_kb.md`, `README.md` ("Evaluate the extraction" section), `docs/phases/agent-mode-demo.md` (eval steps), version pins as in commit 2104821 (`pyproject.toml`, `uv.lock`, `README.md`, `docs/phases/phase1-demo.md`, `docs/phases/phase2-demo.md`, plus agent-mode-demo.md)
- Test: `tests/agents/test_playbook.py`, `tests/e2e/test_agent_mode.py`, `tests/e2e/test_eval.py` (new)

**Interfaces:**
- Consumes: everything above.
- Playbook additions. Insert them after step 4, renumber, and keep every existing sentence verbatim:
  - "Before freezing a baseline, and after adding or changing documents, evaluate the extraction: when `kb todo` lists `eval:` items, follow them in order; do the blind extraction in a fresh session or subagent that does not read the existing records."
  - "Judge each evaluation item on its own and over-flag; never decide a finding yourself — ask the user, then record their answer with `kb eval decide --by <their handle>`."
  - "Put the output of `kb eval sample` in every pull-request description after the baseline."
- [ ] **Step 1: Write the failing tests.**
  - `test_playbook_has_eval_steps`, with the exact sentences.
  - `tests/e2e/test_eval.py::test_full_eval_loop`. On the md fixture with two "must" sentences, only one is drafted. Then: `kb eval extract`, scripted run-b quoting both, `kb eval judge`, scripted verdicts (missing for the uncovered one), `kb eval report --write`. `kb todo` shows one blocking omission ask. Then: draft a record, `kb ingest --report --strict`, `kb eval decide S:<key> --as added --record REQ-F-002 --by @eval-user`. `kb todo` is clean and the freeze ask is offered.
  - Extend `test_agent_follows_playbook_literally` so that the LiteralAgent completes the eval items before freezing.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement** the text and docs, and bump the version to 0.4.0 everywhere 2104821 touched, plus agent-mode-demo.md.
- [ ] **Step 4: Run the full suite** with `uv run pytest -q`; expect all tests to pass. Run `uv run ruff check src`; expect no new errors beyond the 20 pre-existing ones.
- [ ] **Step 5: Commit** with the message `release prep: version 0.4.0 (extraction evaluation layer)`.
