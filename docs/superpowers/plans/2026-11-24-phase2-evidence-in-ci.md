# Phase 2 · Evidence in CI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kb verify` runs on every PR. It works out which claims the PR affects, runs only their linked tests, and derives each claim's state (verified / stale / broken / unverified, E0–E2). It blocks on broken claims, failing invariants, decision edits, missing owner approval and stale context, and posts one PR comment.

**Architecture:**
- Phase 1's deterministic core stays as it is. Phase 2 adds a `kb/verify/` package of small units: `changes` (merge-base vs head), `index` (file cards and anchors, which also serves as the scanner), `affected`, `engine` (a pure function that turns inputs into verdicts), `stale_context`, `approvals` and `run`.
- Three ports are added or extended:
  - `CodeAdapter` gains `fingerprint()` and `imports()`.
  - `TestRunner` is new (a command template plus JUnit parsing).
  - `Forge` is new (`local` file-backed, `github` over REST with urllib).
- Nothing derived is committed (A-1). Results go to `.kb/cache/` and the CI artifact.

**Tech Stack:** as in Phase 1. Python ≥ 3.12, Typer, pydantic v2, tree-sitter 0.27, pytest. New:
- `ruff`, as a **dev-only** dependency pinned to an exact version, used only to prove formatting invariance;
- `tomllib` and `xml.etree` from the standard library.
- No new runtime dependencies.

**Spec:** `kb_spec.md` §7, §12 (E0–E2), §13 (markers), §18, Appendix C/D, and the amendments **A-1, A-3, A-4, A-5, A-9 (per-module `tests`)**. The master plan is `docs/superpowers/plans/2026-11-23-grounded-kb.md`, section "Phase 2".

## Global Constraints

- Fail closed (§2.3). Unknown config keys are errors. A language with no adapter gives `unverified`, never `verified`. A linked test that is missing from the runner's report, or skipped, counts as not passing.
- Verification state is derived and never committed (A-1). Records containing `status: verified`, `fingerprint`, `level` and similar fields stay rejected (pydantic `extra="forbid"`).
- Only deterministic results block (A-3). A PR blocks only on `broken` (below E2 with a failure), `schema`, `decision_guard`, `missing_owner_approval` and `stale_context`, each gated by `policy.fail_on`. Exit 1 only when `policy.mode == "enforce"` or `--strict`.
- Staleness (A-4):
  - A changed fingerprint with passing tests stays **verified**, with the note `changed`.
  - A cited segment whose text changed while the quote is still found is **stale**.
  - A quote that is no longer found is **broken**.
  - An anchor with no adapter is **unverified**.
- Exit codes: `0` ok, `1` policy failure, `2` usage, config or tool error. `--format json` goes to stdout only, and messages go to stderr.
- Output is deterministic: no timestamps, durations, absolute paths or hostnames in `status.json`, JSON output or the comment body, and keys are sorted. The same commit must give byte-identical output under GitHub Actions env vars and in plain bash.
- Tests are run without a shell: the command template is split with `shlex.split`, and the `{tests}` token expands to separate argv items.
- Markers use the Tracey syntax `r[impl ID]` / `r[verify ID]` (A-8). A `r[verify X]` marker inside a test function links that test to X.
- Bot logins matching any glob in `config.bots` (default `["*[bot]"]`) never count as approvers (§2.2).
- The `.kb/cache/` writes reuse the symlink-safe atomic writer. It currently lives as `_write_cache` in `src/kb/ingest/brief.py`. Task 4 moves it to `src/kb/cache.py` as `write_cache(root, rel, text) -> Path`.

## Rulings made while writing this plan

| Ruling | Why | Cost if wrong |
|---|---|---|
| Docstrings are **excluded** from fingerprints, with no config key | The master plan's open decision proposed "no"; a config key is YAGNI | Adding a key later is additive |
| A flaky test (fails, then passes on one of 2 re-runs) is **quarantined**: the claim is `unverified` at E1 with the note `flaky` (a warning, not a block) | §12 says claims "drop to E1"; A-3 says only deterministic failures block, and flakiness is not deterministic | Flip one line in `engine` to make it block |
| A skipped linked test, or one absent from the report, is **broken** | Fail closed; otherwise a test that is silently skipped would pass as proof | Projects with platform-skipped tests see a red claim until they link a test that runs |
| An invariant with no tests is **broken** in `kb verify` (it stays a warning in `kb check`) | §18: "Every invariant has a passing test → CI fails" | A team adopting the tool must add tests to existing INVs before enforcing |
| Config-key anchors and other non-code anchors wait for Phase 4. Phase 2's change detector covers files, symbols, lockfile packages and source segments | Phase 4 owns non-code anchors | None |
| PR comments are posted only through the `github` forge. GitLab CI prints the same Markdown to the job log and keeps it as an artifact | Only `github` and `local` forges are in scope | A GitLab forge can be added later |
| The runner is chosen by the record's module first (a feature's `module`; for an invariant, its first configured module in `governs`), then by the module containing the test file | Test files usually sit outside module paths (`tests/`) | A test run with the wrong module's command shows as a failure, which is visible, not silent |
| Package names are matched to imports by normalising them (lowercase, `-` and `.` → `_`) | No dependency metadata is available offline | Packages whose import name differs (`python-dateutil` → `dateutil`) re-check more than needed, never less: an unmatched changed package re-checks every claim in modules that have Python files |

## Review Focus

1. **A test parametrised by pytest (`test_x[a]`, `test_x[b]`)** must count as passing only when every parametrised case passes (Task 5, `test_parametrized_cases_all_must_pass`).
2. **A test command template with spaces or quotes in a test ID** must not split or inject (Task 5, `test_test_ids_are_separate_argv_items`).
3. **A file with syntax errors on the head side** must not crash fingerprinting. The claim shows broken or verified on what resolves, and the message mentions the syntax errors (Task 7, `test_syntax_error_file_reports_hint`).
4. **A shallow clone where the merge-base is missing** must exit 2 with the `fetch-depth: 0` hint, not report every claim as changed (Task 10, `test_shallow_clone_exits_2_with_hint`).
5. **A review on an older commit**, or approved and later "changes requested", must not count (Task 9, `test_only_latest_review_on_head_counts`).

---

### Task 1: Python fingerprints

**Files:**
- Modify: `src/kb/ports/code.py`. `CodeAdapter` gains `fingerprint(self, source: bytes, symbol: Symbol) -> str`.
- Modify: `src/kb/code/python_pack.py`
- Modify: `pyproject.toml` (dev group: `ruff==<exact version uv resolves>`)
- Test: `tests/code/test_fingerprint.py`

**Interfaces:**
- Produces: `PythonPack.fingerprint(source, symbol) -> str`, a 16-hex-character prefix of sha256 over the normalised token stream of the symbol's node (`source[symbol.start_byte:symbol.end_byte]` re-parsed in the context of the full tree).
- Produces: `fingerprint_of(root: Path, anchor: str, source: bytes | None = None) -> str | None` in `src/kb/code/registry.py`. It returns None when the anchor does not resolve, and raises NoAdapter or UnsafePath like `resolve_anchor`. With `source` given, it fingerprints those bytes instead of reading the file (Task 3 uses this for the base side).

**Normalisation** is a token stream from a pre-order walk of the symbol's tree-sitter node. For each **leaf**, emit `type` and, for identifiers, numbers and operators, its text. Leave out:
- `comment` nodes;
- the docstring: the first statement of a function or class body, when it is an `expression_statement` whose only child is a `string`;
- a `,` leaf whose next named-or-unnamed sibling is a closing `)`, `]` or `}` (trailing commas);
- the `(` and `)` of a `parenthesized_expression` whose parent is not a `call`, `argument_list` or `generator_expression` (redundant parentheses).

A `string` node is emitted as one token, `str:` + `repr(ast.literal_eval(node_text))` (string values decoded, so quote style does not matter). When `literal_eval` fails (an f-string), emit the raw text with its quote characters normalised to `"`. Decorators are part of a decorated definition's node and are included.

- [ ] **Step 1: Write the failing tests.**

```python
SRC = b'def remove_seat(seat, *, refund=False):\n    """Remove."""\n    # note\n    return (charge(seat, \'x\',))\n'

def fp(src: bytes, name="remove_seat"):
    pack = PythonPack()
    sym = next(s for s in pack.symbols("billing/seats.py", src) if s.name == name)
    return pack.fingerprint(src, sym)

def test_fingerprint_is_16_hex(): assert re.fullmatch(r"[0-9a-f]{16}", fp(SRC))
def test_comments_docstring_quotes_trailing_comma_parens_ignored():
    assert fp(SRC) == fp(b'def remove_seat(seat, *, refund=False):\n    return charge(seat, "x")\n')
def test_logic_change_changes_fingerprint():
    assert fp(SRC) != fp(SRC.replace(b"refund=False", b"refund=True"))
def test_string_value_change_changes_fingerprint():
    assert fp(SRC) != fp(SRC.replace(b"'x'", b"'y'"))
def test_call_parentheses_are_kept():
    assert fp(b"def f():\n    return g()\n", "f") != fp(b"def f():\n    return g\n", "f")
def test_method_fingerprint_independent_of_sibling_methods(): ...  # adding a sibling method leaves C.m unchanged
def test_ruff_format_changes_no_fingerprint(tmp_path):
    # write tests/fixtures/fingerprint/unformatted.py (≥ 5 functions: long lines, single quotes, trailing commas,
    # comments, nested calls), run `uv run ruff format <copy>` via subprocess, and compare every symbol's fingerprint
    ...
def test_fingerprint_of_resolves_anchor(tmp_path): ...  # None for a missing name; NoAdapter for "x.rb#f"
```

- [ ] **Step 2: Run** `uv run pytest tests/code/test_fingerprint.py -q`. Expected: FAIL (no `fingerprint`).
- [ ] **Step 3: Implement** `PythonPack.fingerprint` and `registry.fingerprint_of`. Add ruff to the dev group with an exact pin.
- [ ] **Step 4: Run** `uv run pytest tests/code -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(code): python fingerprints that ignore formatting, comments and docstrings`.

### Task 2: Imports and lockfile packages (dependency hop)

**Files:**
- Modify: `src/kb/ports/code.py`. `CodeAdapter` gains `imports(self, path: str, source: bytes) -> list[str]`.
- Modify: `src/kb/code/python_pack.py`
- Create: `src/kb/deps.py`
- Test: `tests/code/test_imports.py`, `tests/test_deps.py`

**Interfaces:**
- Produces: `PythonPack.imports(path, source) -> list[str]`. These are the sorted, unique, fully dotted modules imported anywhere in the file:
  - `import a.b` gives `a.b`;
  - `from a.b import c` gives `a.b` and `a.b.c`;
  - a relative `from . import x` inside `services/billing/seats.py` gives `services.billing.x` (resolved against the file's package).
- Produces, in `kb/deps.py`:
  - `module_name(path: str) -> str | None`: `services/billing/seats.py` gives `services.billing.seats`, `pkg/__init__.py` gives `pkg`, and anything other than `.py` gives None.
  - `LOCKFILES = ("uv.lock", "poetry.lock", "requirements.txt")`, plus any `requirements*.txt` at any depth.
  - `is_lockfile(path: str) -> bool`.
  - `lockfile_packages(path: str, data: bytes) -> dict[str, str]`, mapping normalised name to version. TOML files are read with `[[package]] name/version`. Requirements files are read line by line as `name==version`, ignoring comments, `-r`, `-e` and markers after `;`.
  - `normalise_package(name: str) -> str`: lowercase, with `-` and `.` changed to `_`.
  - `changed_packages(base: dict[str, str], head: dict[str, str]) -> set[str]`: names added, removed or with a changed version.
  - `imported_top_levels(imports: list[str]) -> set[str]`: the normalised first dotted component of each import.

- [ ] **Step 1: Write the failing tests.**
  - `test_imports_absolute_from_and_relative` covers all three forms above.
  - `test_imports_inside_functions_count`.
  - `test_module_name_init_and_non_python`.
  - `test_uv_lock_and_poetry_lock_parse`, on small inline TOML.
  - `test_requirements_txt_parse_ignores_comments_and_markers`: `stripe==7.1.0 ; python_version>"3"` gives `{"stripe": "7.1.0"}`.
  - `test_changed_packages_added_removed_bumped`.
  - `test_normalise_package`: `Python-Dateutil` gives `python_dateutil`.
- [ ] **Step 2: Run** `uv run pytest tests/code/test_imports.py tests/test_deps.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Use a tree-sitter query over `import_statement`, `import_from_statement` and `relative_import`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(code): imports and lockfile package diff for the dependency hop`.

### Task 3: Change detector (merge-base vs head)

**Files:**
- Create: `src/kb/verify/__init__.py` (empty), `src/kb/verify/changes.py`
- Test: `tests/verify/test_changes.py`

**Interfaces:**
- Consumes: `gitfiles.changed_since`, `gitfiles.file_at`, `KbRepo.at_ref`, `KbRepo.load`, `registry.adapter_for`, `PythonPack.fingerprint` (Task 1) and `deps.*` (Task 2).
- Produces:

```python
SymbolChange = Literal["added", "removed", "changed"]

@dataclass(frozen=True)
class ChangeSet:
    base_ref: str                       # the merge-base commit
    files: tuple[str, ...]              # sorted; changed_since(root, base_ref) (working tree vs base, plus untracked)
    symbols: dict[str, SymbolChange]    # anchor "path#name" -> change, for files with an adapter
    fingerprints: dict[str, tuple[str | None, str | None]]  # anchor -> (base fp, head fp) for every symbol in changed files
    segments: dict[str, SymbolChange]   # "SRC-x#seg-NN" -> change, by segment id, comparing hash
    packages: frozenset[str]            # normalised names changed in any lockfile

def compute_changes(root: Path, base_ref: str, head_repo: KbRepo | None = None) -> ChangeSet: ...
```

The head side is the working tree. The base side is read with `file_at(root, base_ref, path)` and `KbRepo.at_ref(root, base_ref)`. There is no stored fingerprint: both sides are computed (A-1). A file that exists on only one side gives `added`/`removed` symbols. A file with no adapter contributes only to `files`.

- [ ] **Step 1: Write the failing tests** with `make_git_repo` from `tests/helpers.py`. Commit the base, then edit the working tree.
  - `test_refactor_marks_symbol_changed`: the body of `remove_seat` changes, so `symbols["services/billing/seats.py#remove_seat"] == "changed"` and the two fingerprints differ.
  - `test_format_only_change_marks_nothing`: whitespace and quote-style changes, so the file is in `files` but `symbols == {}`.
  - `test_rename_is_removed_plus_added`.
  - `test_segment_text_change_marks_segment_changed`: rewrite `kb/sources/SRC-x/segments.jsonl` with a new hash for seg-08, so `segments == {"SRC-x#seg-08": "changed"}`.
  - `test_lockfile_bump_lists_package`: in uv.lock, stripe 7.0 → 7.1 gives `packages == {"stripe"}`.
  - `test_untracked_file_counts_as_added`.
- [ ] **Step 2: Run** `uv run pytest tests/verify/test_changes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `compute_changes`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(verify): change detector computing both sides from git`.

### Task 4: Anchor index and file cards (scanner)

**Files:**
- Create: `src/kb/cache.py`. Move `_write_cache` out of `src/kb/ingest/brief.py` as `write_cache(root: Path, rel: str, text: str) -> Path`, and update brief.py to import it.
- Create: `src/kb/verify/index.py`
- Test: `tests/verify/test_index.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class FileCard:
    path: str
    modules: tuple[str, ...]            # config.modules whose paths match (validate.modules_for)
    symbols: tuple[tuple[str, str, str], ...]  # (anchor, kind, fingerprint), sorted by anchor
    imports: tuple[str, ...]
    markers: tuple[tuple[str, str, int, str | None], ...]  # (kind, id, line, enclosing test ref "path::name" or None)

@dataclass
class AnchorIndex:
    cards: dict[str, FileCard]          # every evidence file (markers.evidence_files) with an adapter, plus files with markers
    def tests_for(self, record_or_claim_id: str) -> list[str]:  # sorted test refs whose function holds r[verify ID]
    def impl_files(self, record_or_claim_id: str) -> list[str]:
    def importers_of(self, path: str) -> list[str]:  # files whose imports name module_name(path) or a submodule of it
    def to_json(self) -> str:           # sort_keys, indent=2, deterministic

def build_index(root: Path, config: KbConfig) -> AnchorIndex: ...
INDEX_PATH = ".kb/cache/index.json"
```

A test ref for a marker is the innermost `test`-kind symbol whose byte range holds the marker's line. It is written `path::name`, with `.` in a method name written as `::`, which matches `resolve_test`. These are structure claims, true by construction: they come from the engine and are never committed (A-11).

- [ ] **Step 1: Write the failing tests.**
  - `test_marker_inside_test_function_links_test`: `# r[verify REQ-F-002]` inside `def test_no_refund_on_removal` gives `tests_for("REQ-F-002") == ["tests/test_seats.py::test_no_refund_on_removal"]`.
  - `test_marker_in_method_uses_double_colon`: `TestSeats.test_x` gives `tests/test_seats.py::TestSeats::test_x`.
  - `test_marker_outside_test_has_no_ref`.
  - `test_importers_of_one_hop`.
  - `test_to_json_is_deterministic`: two builds give identical bytes.
  - `test_cards_skip_kb_and_source_docs`.
- [ ] **Step 2: Run** `uv run pytest tests/verify/test_index.py tests/ingest -q`. Expected: FAIL on the new tests only.
- [ ] **Step 3: Implement.** Move `write_cache`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(verify): anchor index and file cards`.

### Task 5: TestRunner port, command runner, pytest preset, flaky re-runs

**Files:**
- Modify: `src/kb/config/schema.py`. Add `TestsCfg`, and `ModuleCfg.tests: TestsCfg | None = None`.
- Create: `src/kb/ports/tests.py`, `src/kb/testrun.py`
- Modify: `src/kb/init/templates/kb_readme.md`, to document `modules[].tests`.
- Test: `tests/test_testrun.py`, `tests/config/test_load.py` (new cases)

**Interfaces:**
- Produces the config model:

```python
class TestsCfg(_Model):
    preset: Literal["pytest"] | None = None
    run: str | None = None              # e.g. "pytest {tests} --junitxml={junit}"; needs both {tests} and {junit}
    id_format: str = "{path}::{name}"   # {path} file, {name} "Class::func" or "func"
    timeout_seconds: int = Field(900, ge=1)
    # model_validator: exactly one of preset / run; run must contain "{tests}" and "{junit}"
```

`PYTEST_PRESET = "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 {tests} --junitxml={junit}"`. It runs the project's own `python` from PATH, never kb's interpreter.

- Produces the port, in `kb/ports/tests.py`:

```python
Outcome = Literal["passed", "failed", "skipped", "missing", "flaky"]

@dataclass(frozen=True)
class TestRun:
    outcomes: dict[str, Outcome]        # test ref -> outcome, one per requested ref
    log_tail: str                       # last 4000 chars of stdout+stderr of the first run (stderr only, never in JSON)

class TestRunner(Protocol):
    def run(self, root: Path, refs: list[str]) -> TestRun: ...
```

- Produces, in `kb/testrun.py`:
  - `CommandRunner(cfg: TestsCfg)`.
  - `parse_junit(xml: bytes, refs: list[str]) -> dict[str, Outcome]`.
  - `runner_for(config: KbConfig, module_id: str | None) -> CommandRunner | None`.
  - `run_tests(root, config, refs_by_module: dict[str | None, list[str]], reruns: int = 2) -> dict[str, Outcome]`. Refs under `None` (no module with a `tests` config) map to `missing`, and the caller turns that into an `unverified` reason "no test command: set modules[].tests".

**Junit matching.** A ref `path::[Class::]func` matches a `<testcase>` when both of these hold:
- its `name`, with any `[...]` parametrisation suffix stripped, equals `func`;
- either the `file` attribute equals `path`, or `classname` equals `path` with `/` → `.` and `.py` removed, plus `.Class` when there is a class.

All matching cases must pass:
- a `<failure>` or `<error>` makes the test `failed`;
- `skipped` only, with no pass, is `skipped`;
- no matching case is `missing`.

**Flaky.** After the first run, the refs that came out `failed` are run again up to `reruns` times, each time on their own. If any re-run passes, the ref is `flaky`.

**Execution.** `argv = shlex.split(template)`. The single argv item equal to `{tests}` is replaced by one argv item per formatted ID, and `{junit}` is replaced, wherever it occurs, by a temp file path. Run with `subprocess.run(argv, cwd=root, timeout=cfg.timeout_seconds, capture_output=True, env=<os.environ minus GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE>)`. A timeout, or a missing junit file, makes every requested ref `failed`, and `log_tail` says why. An executable that is not found raises `TestRunError` (exit 2).

- [ ] **Step 1: Write the failing tests.** Build a tiny pytest project in `tmp_path`, with `tests/test_a.py` holding a pass, a fail, a skip and a parametrised pass/fail pair.
  - `test_pytest_preset_outcomes`: pass gives `passed`, fail gives `failed`, skip gives `skipped`, and an unknown ref gives `missing`.
  - `test_parametrized_cases_all_must_pass`.
  - `test_flaky_detected_after_rerun`: a test that fails when the file `.flag` is absent, and creates it, gives `flaky`.
  - `test_test_ids_are_separate_argv_items`: a `run` of `python record_argv.py {tests} --junitxml={junit}` gets IDs with spaces and `;` as separate argv entries, and nothing runs in a shell.
  - `test_timeout_marks_all_failed`.
  - `test_tests_cfg_needs_exactly_one_of_preset_or_run`.
  - `test_run_template_needs_tests_and_junit_tokens`.
  - `test_unknown_tests_key_rejected`.
- [ ] **Step 2: Run** `uv run pytest tests/test_testrun.py tests/config -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tests): TestRunner port with a command runner, pytest preset and flaky re-runs`.

### Task 6: Affected selection

**Files:**
- Create: `src/kb/verify/affected.py`
- Test: `tests/verify/test_affected.py`

**Interfaces:**
- Consumes `ChangeSet` (Task 3) and `AnchorIndex` (Task 4).
- Produces:

```python
@dataclass(frozen=True)
class Affected:
    claims: frozenset[str]        # FEAT-x.Cn
    requirements: frozenset[str]
    invariants: frozenset[str]

def affected(changes: ChangeSet | None, config: KbConfig, repo: KbRepo, index: AnchorIndex) -> Affected: ...
```

`changes=None` means `--all`: every live claim, requirement and invariant. Otherwise a live record or claim is affected when any of these hold:
1. A file it points to is in `changes.files`: anchor file, linked test file, or a file holding a marker for it.
2. Its anchor file imports a changed file (`index.importers_of`, one hop).
3. Its module (the feature's module; the requirement's modules and its features' modules; the invariant's `governs`) contains a file whose `imported_top_levels` intersects `changes.packages`.
4. Its record file changed (`repo.paths`), or a quote cites a segment in `changes.segments`.
5. `kb.config.yaml` changed. That makes **everything** affected.

A requirement is also affected when any claim with `requirement == rid` is affected.

- [ ] **Step 1: Write the failing tests.**
  - `test_anchor_file_change_affects_claim_and_requirement`.
  - `test_lockfile_bump_affects_only_importing_module`: MOD-billing imports `stripe` and MOD-sessions does not, so a stripe bump affects only MOD-billing's claims.
  - `test_segment_change_affects_citing_requirements`.
  - `test_importer_hop`.
  - `test_config_change_affects_everything`.
  - `test_all_mode`.
  - `test_superseded_records_not_affected`.
- [ ] **Step 2: Run** `uv run pytest tests/verify/test_affected.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(verify): affected claims from changes, imports and lockfiles`.

### Task 7: Derived status engine (A-4)

**Files:**
- Create: `src/kb/verify/engine.py`
- Test: `tests/verify/test_engine.py`

**Interfaces:**
- Produces:

```python
State = Literal["verified", "stale", "broken", "unverified", "built", "not_built"]

@dataclass(frozen=True)
class Verdict:
    id: str
    kind: Literal["claim", "requirement", "invariant"]
    status: State
    level: Literal["E0", "E1", "E2"]
    reasons: tuple[str, ...]      # sentences, in rule order below
    notes: tuple[str, ...]        # sorted subset of {"changed", "co-changed", "flaky"}
    tests: tuple[str, ...]        # sorted test refs considered

@dataclass(frozen=True)
class EngineInput:
    root: Path
    config: KbConfig
    repo: KbRepo                  # head
    base_repo: KbRepo | None
    changes: ChangeSet | None
    index: AnchorIndex
    outcomes: dict[str, Outcome]  # every ref in tests_needed(...) for the affected set

def tests_needed(inp_without_outcomes, affected: Affected) -> dict[str | None, list[str]]:  # by runner module
def derive(inp: EngineInput, affected: Affected) -> list[Verdict]:  # sorted by (kind order claim<requirement<invariant, id)
```

**Claim rules**, applied in order. The first rule that ends the derivation sets the status.
1. There is no `code` and no `r[impl <claim id>]` marker: `unverified`, E0, "no code anchor".
2. `NoAdapter`: `unverified`, E0, "no code adapter for <file>". `UnsafePath`: `broken`.
3. The anchor does not resolve: `broken`, E0, "anchor <ref> no longer resolves". Add the suffix "; file has syntax errors" when that is true.
4. Otherwise the level is E1. Add the note `changed` when `changes.fingerprints[anchor]` has two different non-None values.
5. The tests are `claim.test` plus `index.tests_for(claim.id)`. With none: `unverified`, E1, "no linked test".
6. A linked test does not resolve (`resolve_test`): `broken`, E1, "linked test <ref> was deleted".
7. Any outcome is `failed`, `skipped` or `missing`: `broken`, E1, "<ref> <outcome>" for each.
8. Any outcome is `flaky`: `unverified`, E1, with the note `flaky`, "<ref> is flaky; quarantined".
9. Otherwise `verified`, E2.

Add the note `co-changed` when both the anchor file and a linked test file are in `changes.files`.

**Requirement rules.**
- Intent side:
  - Any quote not found at head, or a cited segment that is missing at head, makes it `broken`. Use the existing `kb.quotes.match.find_quote` over the segment slice.
  - Otherwise, a cited segment in `changes.segments` as `changed` makes it `stale`, with "cited segment <ref> changed; needs intent approvers' review".
- Code side, over its live claims and its own markers:
  - any claim verdict `broken` gives `broken`;
  - no impl evidence (no claim anchor resolves and no `r[impl rid]`) gives `not_built`;
  - tests are its claims' tests plus `index.tests_for(rid)`; with none, `built`;
  - otherwise the test outcomes give the same results as claim rules 6–9.
- Overall: `broken`, if either side is broken, beats `stale`, which beats the code-side status.
- Level: E2 when verified, E1 when built, E0 otherwise.

**Invariant rules.**
- A live INV with no tests is `broken`, "invariant lists no tests".
- Otherwise claim rules 6–9 apply to its tests plus `index.tests_for(inv id)`.

- [ ] **Step 1: Write the failing tests.** Table-driven over small in-memory repos, with outcomes injected; no subprocess.
  - One test per rule. Name each by its rule, for example `test_changed_fingerprint_passing_tests_is_verified_with_note`, `test_deleted_test_is_broken_never_lower_level`, `test_flaky_quarantined_e1`, `test_segment_changed_quote_found_is_stale`, `test_quote_gone_is_broken`, `test_no_adapter_is_unverified` and `test_invariant_without_tests_broken`.
  - `test_syntax_error_file_reports_hint`.
  - `test_verdicts_sorted_and_deterministic`.
- [ ] **Step 2: Run** `uv run pytest tests/verify/test_engine.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `derive` and `tests_needed`. Choose the runner module as in the rulings table.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(verify): derived claim, requirement and invariant states (A-4)`.

### Task 8: Stale-context check (A-5)

**Files:**
- Create: `src/kb/verify/stale_context.py`
- Test: `tests/verify/test_stale_context.py`

**Interfaces:**
- Consumes `context.governing(root, config, repo, paths=...)` and `gitfiles.find_default_branch`, `merge_base`, `rev_parse`, `read_tree_files` and `KbRepo.at_ref`.
- Produces `stale_context(root: Path, config: KbConfig, repo: KbRepo, base_branch: str, merge_base_ref: str, changed: list[str]) -> list[Issue]`.

`tip = rev_parse(base_branch)`. Find the record files under `kb/` that differ between `merge_base_ref` and `tip` (`git diff --name-only merge_base tip -- kb/`), and map them to record IDs through both `KbRepo.at_ref(tip)` paths and `base_repo` paths. Compute the governing IDs as the union of `governing(..., repo=head)` and `governing(..., repo=KbRepo.at_ref(tip))` for `paths=changed`: decisions, invariants, requirements and claims' features. Report one error per intersecting ID:

`Issue("stale_context", f"{rid} changed on {base_branch} after this branch forked; rebase on {base_branch} and re-run `kb context`", path=<record path>, record=rid)`

When `tip == merge_base_ref`, return `[]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_pr_branched_before_adr_merged_fails`: ADR-0041 governing MOD-billing lands on main after the branch forks, and the branch edits `services/billing/seats.py`, so there is one `stale_context` issue for ADR-0041.
  - `test_passes_after_rebase`.
  - `test_unrelated_record_change_does_not_fail`: an ADR governing MOD-sessions.
  - `test_no_default_branch_progress_passes`.
- [ ] **Step 2: Run** `uv run pytest tests/verify/test_stale_context.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(verify): stale-context check against the default branch`.

### Task 9: Forge port (local, github) and owner approval

**Files:**
- Create: `src/kb/ports/forge.py`, `src/kb/forge/__init__.py`, `src/kb/forge/local.py`, `src/kb/forge/github.py`, `src/kb/verify/approvals.py`
- Test: `tests/forge/test_local.py`, `tests/forge/test_github.py` (urllib is monkeypatched; no network), `tests/verify/test_approvals.py`

**Interfaces:**
- Produces the port:

```python
@dataclass(frozen=True)
class PullRequest: number: int; head_sha: str; author: str
@dataclass(frozen=True)
class Review: login: str; state: str; commit_id: str; submitted_at: str   # ISO 8601; state as GitHub spells it

class Forge(Protocol):
    name: str
    def pull_request(self) -> PullRequest | None: ...
    def reviews(self, pr: PullRequest) -> list[Review]: ...
    def team_members(self, handle: str) -> set[str] | None: ...   # "@org/team" -> logins; None = cannot read
    def upsert_comment(self, pr: PullRequest, body: str) -> None:  # one comment, found by COMMENT_MARKER
COMMENT_MARKER = "<!-- kb-verify -->"
```

- Produces `forge_from_option(value: str, root: Path) -> Forge | None` in `kb/forge/__init__.py`. It accepts:
  - `none`;
  - `local:<path>`;
  - `github`;
  - `auto`: `github` when `GITHUB_ACTIONS == "true"` and the event payload at `GITHUB_EVENT_PATH` has `pull_request`, otherwise None.

  Anything else raises `ValueError` (exit 2).
- `local`: a JSON file `{"pr": {...} | null, "reviews": [...], "teams": {"@org/team": ["login"]}, "comments": [str]}`. A team missing from `teams` gives None. `upsert_comment` replaces the comment that holds the marker, or appends one, and writes the file back.
- `github`: reads `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `GITHUB_EVENT_PATH` and `GITHUB_API_URL` (default `https://api.github.com`).
  - Reviews come from `GET /repos/{repo}/pulls/{n}/reviews?per_page=100`, paginated through the `Link` header.
  - Team members come from `GET /orgs/{org}/teams/{slug}/members?per_page=100`. A 403 or 404 gives None.
  - The comment is updated with `GET /repos/{repo}/issues/{n}/comments` to find the marker, then `PATCH` or `POST`.
  - Requests use `urllib.request` with a 30 s timeout. Any other HTTP error raises `ForgeError` (exit 2).
- Produces, in `kb/verify/approvals.py`: `required_owners(root, config, repo, base_repo, changes, verdicts) -> dict[str, list[str]]`, mapping a handle to the reasons it is required, and `owner_approval(forge, pr, config, required) -> list[Issue]`.

**Required handles:**
- **Changed files:** the owners from CODEOWNERS (`parse_codeowners` and `owners_for`, last match wins) for each changed file, when a CODEOWNERS file exists at the repo root, `.github/` or `docs/`. Otherwise the module owners, through `resolve_owner`.
- **Requirements:** changed or new requirement records, and requirement verdicts that are `stale`, need `policy.approvers.intent`.
- **Decisions and invariants:** changed or new ADR and INV records need `policy.approvers.decisions`.

**Rule.**
1. For each reviewer, keep only the latest review by `submitted_at`, ignoring `COMMENTED`. It counts only when its state is `APPROVED`, its `commit_id == pr.head_sha`, the login is not the PR author and the login matches no glob in `config.bots`.
2. A handle `@user` is satisfied when that login approved.
3. A handle `@org/team` is satisfied when `team_members` holds an approving login. If any team's membership comes back None, emit one warning: `Issue("owner_approval.deferred", "cannot read team membership for <handles>; relying on branch protection's required code-owner review", "warning")`. Do not fail on it.
4. Each unsatisfied handle gives `Issue("missing_owner_approval", f"{handle} has not approved {pr.head_sha[:7]} ({reasons})")`.

- [ ] **Step 1: Write the failing tests.**
  - `test_only_latest_review_on_head_counts`: APPROVED on an old SHA doesn't count, and APPROVED then CHANGES_REQUESTED doesn't count.
  - `test_bot_approval_never_counts`.
  - `test_author_approval_never_counts`.
  - `test_team_membership_unreadable_defers_with_warning`.
  - `test_stale_requirement_requires_intent_approvers`.
  - `test_adr_change_requires_decision_approvers`.
  - `test_codeowners_last_match_wins`.
  - `test_local_forge_upserts_one_comment`.
  - `test_github_paginates_reviews_and_maps_404_to_none`: a fake `urlopen` serves canned responses.
  - `test_auto_is_none_outside_actions`.
- [ ] **Step 2: Run** `uv run pytest tests/forge tests/verify/test_approvals.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(forge): local and github forges with the missing_owner_approval rule`.

### Task 10: `kb verify`, outputs, exit codes, and `kb status` verified

**Files:**
- Create: `src/kb/verify/run.py`, `src/kb/verify/report.py`
- Modify: `src/kb/cli.py` (the `verify` command, and the `status` output), `src/kb/status.py`
- Test: `tests/cli/test_verify.py`, `tests/test_status.py` (new cases)

**Interfaces:**
- Produces:

```python
@dataclass
class VerifyReport:
    base_ref: str | None               # merge-base commit, None with --all and no base
    head: str                          # HEAD commit
    verdicts: list[Verdict]
    issues: list[Issue]                # run_check issues + verdict issues + stale_context + approval issues, sorted
    blocking: list[Issue]
    mode: str

def run_verify(root: Path, *, base: str | None, all_: bool, forge: Forge | None) -> VerifyReport: ...
def render_json(r: VerifyReport) -> str          # sort_keys, indent=2; no times, no absolute paths, no log tails
def render_markdown(r: VerifyReport) -> str      # starts with COMMENT_MARKER; sections below
STATUS_PATH = ".kb/cache/status.json"; COMMENT_PATH = ".kb/cache/verify.md"
```

**CLI:** `kb verify [--base REF] [--all] [--format text|json|markdown] [--forge auto|none|github|local:PATH] [--comment] [--strict]`.
- With no `--base` and no `--all`, the base is `resolve_base(root)`. When that is None, act as `--all`.
- `--comment` posts `render_markdown` through the forge. With no PR it prints "no pull request; comment skipped" to stderr.
- It always writes `STATUS_PATH` (as `render_json`) and `COMMENT_PATH`.

**Order of work in `run_verify`:**
1. `run_check(root, base=base_ref_name)`, so the schema gates and `decision_guard` run.
2. `compute_changes`.
3. `build_index`, then write `INDEX_PATH`.
4. `affected`.
5. `tests_needed`, then `run_tests`.
6. `derive`.
7. `stale_context`, when a base is set.
8. `required_owners` and `owner_approval`, when the forge gives a PR.

**Verdict issues:**
- `broken` gives an error, `f"{kind}.broken"`;
- `stale` gives a warning, `requirement.stale`;
- `unverified` gives a warning, `f"{kind}.unverified"`.

**Category for `policy.fail_on`:**

| Issue codes | Category |
|---|---|
| `*.broken` | `broken` |
| `decision_guard` | `decision_guard` |
| `missing_owner_approval` | `missing_owner_approval` |
| `stale_context` | `stale_context` |
| any other error | `schema` |

`blocking` holds the error issues whose category is in `fail_on`. The exit code is `1` when `blocking` is non-empty and the mode is `enforce` or `--strict` is given. `ConfigError`, `GitError`, `TestRunError` and `ForgeError` exit `2`.

**Markdown sections:**
1. A headline: `**kb verify**: N broken · N stale · N unverified · N verified (mode: …)`.
2. `### Blocking`.
3. `### Claims`: a table with columns `ID | status | level | notes | reason`. It lists only affected items, ordered broken, stale, unverified, then the rest, and caps at `config.review.max_items_per_pr` rows with "… and N more".
4. `### Approvals`.
5. `### Other issues`.

**`kb status`:** when `STATUS_PATH` exists and its `head` equals the current HEAD and the tree is clean (`is_dirty(root, ".")` is False), the per-module line gains `, V/N verified`. Otherwise the line is unchanged and a stderr note says "run `kb verify` for verified counts".

- [ ] **Step 1: Write the failing CLI tests** on a git fixture built from `tests/fixtures/kb_valid` with a pytest preset on MOD-billing and a `local:` forge file.
  - `test_verify_all_green_exit_0_and_writes_status_json`.
  - `test_broken_claim_exit_1_in_enforce_0_in_warn_only`.
  - `test_json_stdout_only_messages_stderr`.
  - `test_markdown_starts_with_marker_and_caps_rows`.
  - `test_comment_upserted_via_local_forge`.
  - `test_shallow_clone_exits_2_with_hint`: `--base` names a ref with no merge-base, so exit 2 and stderr contains `fetch-depth: 0`.
  - `test_status_shows_verified_after_verify_on_clean_head`.
  - `test_unknown_forge_value_exits_2`.
- [ ] **Step 2: Run** `uv run pytest tests/cli/test_verify.py tests/test_status.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: the whole suite passes.
- [ ] **Step 5: Commit** `feat(cli): kb verify with status.json, PR comment and policy exit codes`.

### Task 11: CI templates and determinism

**Files:**
- Modify: `src/kb/init/templates/github_workflow.yml`, `src/kb/init/templates/gitlab_ci.yml`, `src/kb/init/templates/kb_readme.md`, `README.md`
- Test: `tests/init/test_init.py` (new template assertions), `tests/cli/test_verify_determinism.py`

**Template changes:**
- **GitHub, on PRs:**
  - add `permissions: pull-requests: write`;
  - add the comment `# install your project's test dependencies here`;
  - run `uvx --from "$KB_INSTALL_SPEC" kb verify --base "origin/$BASE_REF" --comment --forge auto`, with `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` in `env`;
  - add `actions/upload-artifact@v4` for `.kb/cache/status.json` and `.kb/cache/verify.md` with `if: always()`.
- **GitHub, on push:** `kb verify --all`.
- **GitLab:** the MR job runs `kb verify --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --format markdown` and keeps `artifacts: paths: [.kb/cache/status.json, .kb/cache/verify.md]` with `when: always`. The default-branch job runs `kb verify --all`.
- Both keep `fetch-depth: 0` / `GIT_DEPTH: "0"` and the empty-install-spec warning.

- [ ] **Step 1: Write the failing tests.**
  - `test_github_template_runs_verify_with_comment_and_artifact`: string assertions on the rendered workflow, and `yaml` loads it.
  - `test_gitlab_template_runs_verify_and_keeps_artifacts`.
  - `test_verify_json_identical_under_actions_env_and_plain`: run `kb verify --all --format json --forge none` twice through `subprocess` on the same commit:
    - once with `GITHUB_ACTIONS=true CI=true RUNNER_TEMP=<tmp1> TZ=UTC LANG=C.UTF-8 HOME=<tmp1>`;
    - once with a minimal env (`PATH`, `HOME=<tmp2>`, `TZ=Asia/Karachi`).

    Assert the stdout bytes are equal, and so are `.kb/cache/status.json`'s.
- [ ] **Step 2: Run** `uv run pytest tests/init tests/cli/test_verify_determinism.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the templates and fix any nondeterminism found.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ci): workflows run kb verify, comment on PRs and keep status artifacts`.

### Task 12: Phase 2 acceptance, staleness eval, demo doc

**Files:**
- Create: `tests/e2e/test_phase2.py`, `eval/staleness.py`, `docs/phases/phase2-demo.md`
- Create: `eval/results/staleness.json` (written by the eval, then committed)
- Test: `tests/test_eval.py` (new case for the staleness runner)

The **acceptance tests** each start from a git fixture with `kb_valid`, a pytest preset and a `local:` forge whose reviews approve the head SHA. Each test is one exit criterion:
- `test_appendix_c_refactor_verified_with_changed_note`: refactor `remove_seat` while the tests still pass, so FEAT-001.C1 is `verified` with the note `changed`.
- `test_rename_breaks_claim_and_fails_pr`, under `mode: enforce`.
- `test_ruff_format_changes_no_fingerprint_end_to_end`: run `ruff format` on `services/`, so `kb verify` gives no `changed` notes.
- `test_deleting_linked_test_breaks_claim`: the claim is `broken`, never E1 `unverified`.
- `test_flaky_test_quarantined`.
- `test_lockfile_bump_rechecks_only_importing_module`: assert that the affected claims are a subset of MOD-billing's.
- `test_appendix_d_segment_wording_change_makes_requirements_stale_and_needs_intent_approvers`: edit seg-08's text in the stored source, which passes through `kb source sync` on a Markdown doc. The requirements citing it become `stale`, and with no intent approval in the forge file there is `missing_owner_approval` for `@acme/product`.
- `test_stale_context_before_rebase_fails_after_rebase_passes`.
- `test_invariant_failing_test_fails_pr`.

**`eval/staleness.py`** applies 20 seeded changes, one per throwaway copy, to the `kb_valid` git fixture. Each change carries its expected label set, over `{changed, stale, broken, none}` per claim or requirement. The changes are:
- 5 refactors;
- 3 format-only edits;
- 3 renames;
- 3 deleted tests;
- 3 segment rewordings;
- 2 quote removals;
- 1 unrelated file edit.

For each change it runs `run_verify(..., forge=None)`, compares the result with the expected labels, and writes `eval/results/staleness.json` as `{"changes": 20, "precision": p, "recall": r, "per_change": [...]}` with sorted keys. The test `test_staleness_eval_runs_and_writes_results` runs it and asserts `changes == 20` and `0 <= precision <= 1`.

**`docs/phases/phase2-demo.md`** is the manual walkthrough on `/Users/dev/projects/acme-billing`, with the same tables-to-fill style as phase1-demo.md. Its steps:
- add `tests: {preset: pytest}` to the module;
- write `services/billing/seats.py` and its test with markers;
- run `kb verify --all`;
- refactor, then rename, then edit the spec, then run `kb verify` after each.

- [ ] **Step 1: Write** the e2e tests and the eval test.
- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_phase2.py tests/test_eval.py -q`. Fix any gaps they expose, inside the owning module's code.
- [ ] **Step 3: Run** `uv run python eval/staleness.py`, then commit its results file.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `test(e2e): Phase 2 acceptance, staleness eval and demo walkthrough`.
