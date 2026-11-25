# Agent-driven mode (0.3.0): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user talks to their coding agent in plain words, and the agent runs kb end to end: ingest, fix, triage, sync, redraft, check, freeze. kb gives the agent one to-do list that separates what the agent fixes itself from the decisions only a person can make, each with options. The person's answers become a quotable document.

**Architecture:**
- **`kb todo`** (new, `src/kb/todo.py`) combines `kb check`, document-coverage detection and record flags into two lists: `do` (agent-fixable, each with a command) and `ask` (human decisions, each with evidence and options). It also returns a `state`.
- **`kb clarify`** (new, `src/kb/clarify.py`) appends a decision to `docs/clarifications.md` and stores it as a source, so requirements can quote the person's answer.
- **The agent playbook** extends the existing rules: a sixth rule, a "Working with the knowledge base" section in the AGENTS.md block, and a Claude Code skill file. All three tell the agent to loop on `kb todo`.
- **The session-start hook** prepends a one-line to-do summary.
- A new check reports documents under `config.sources` that were never ingested.

**Tech Stack:** as before. Python ≥ 3.12, Typer, pydantic v2, pytest. No new dependencies.

**Spec:** `kb_spec.md`. This plan adds **A-18 · Agent-driven mode** (Task 4), building on A-2, A-7 (`agent` provider), A-12 (agent rules), A-16 and A-17.

## Global Constraints

- **People decide and approve; kb checks; agents do the rest (§2.2).**
  - `kb todo` never answers an `ask` item.
  - `kb clarify` records an answer the agent received from the person. It never invents one.
  - Approval is still a merged PR.
- **Deterministic and machine-readable.**
  - `kb todo --format json` goes to stdout only, with sorted keys and no timestamps.
  - Item ids are stable across runs, for example `vague:REQ-NF-001`.
- **Untrusted text stays data.**
  - Evidence quotes and headings in `kb todo` text output go through `kb.textfmt.inline_code`.
  - JSON carries raw strings.
- **Exit codes:**
  - `kb todo`: `0` always on success, even when the lists are not empty; `2` for config or tool errors.
  - `kb clarify`: `0` ok; `2` for usage or tool errors.
- **The existing behaviour of `kb check`, `kb ingest --report`, `kb verify` and `kb redraft` is unchanged.** The one exception is the new `source.not_ingested` warning.

## Review Focus

1. **A document under `docs/` with no reader** (for example a `.png`) must not be reported as `source.not_ingested`. It is skipped, the same way `kb ingest` skips it (Task 1, `test_unreadable_file_not_reported`).
2. **Instruction-like text inside a quote** shown by `kb todo` must not become markup or instructions. Text output is inline code (Task 2, `test_todo_text_escapes_evidence`).
3. **`kb clarify` text that could forge a new section or a heading** (a newline followed by `#`) must be refused (Task 3, `test_clarify_refuses_headings_and_blank_lines`).
4. **`kb todo` in a repo with no `kb.config.yaml`** must print one `do` item, `kb init`, and exit 0. It must not crash (Task 1, `test_todo_without_config_suggests_init`).
5. **The session-start summary must never break the hook.** Any error in `build_todo` is swallowed, and the existing brief is still printed (Task 4, `test_session_start_survives_todo_error`).

---

### Task 1: Detect documents that were never ingested; `kb todo` core with `do` items

**Files:**
- Create: `src/kb/todo.py`
- Modify:
  - `src/kb/gates/sources.py`: add `source.not_ingested` (warning).
  - `src/kb/cli.py`: add the `todo` command.
- Test: `tests/test_todo.py`, `tests/gates/test_source_not_ingested.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DoItem:
    id: str            # stable, e.g. "sync:SRC-pricing-spec-v3", "ingest:docs/refund-policy.md", "fix:quote.not_found:REQ-F-002"
    code: str          # the issue code it comes from, or "kb.init" / "kb.ingest" / "kb.sync" / "kb.redraft"
    message: str
    command: str | None  # what the agent runs, e.g. "kb source sync", "kb ingest docs/refund-policy.md"
    path: str | None
    record: str | None

@dataclass(frozen=True)
class Option:
    key: str           # short, e.g. "target", "keep", "retire"
    label: str         # what the person sees
    then: str          # what the agent does if chosen (plain sentence, may name commands)

@dataclass(frozen=True)
class AskItem:
    id: str            # stable, e.g. "vague:REQ-NF-001", "owner:policy.approvers.intent", "freeze:v1"
    kind: str          # vague | conflicting | untestable | owner | secret | freeze
    question: str
    evidence: dict     # {} or {"record": id, "statement": ..., "source": "SRC-x#seg-NN", "quote": ..., "document": path, "page": n, "heading": ...}
    options: tuple[Option, ...]

@dataclass(frozen=True)
class Todo:
    do: tuple[DoItem, ...]       # sorted by id
    ask: tuple[AskItem, ...]     # sorted by id
    state: dict                  # {"initialized": bool, "sources": n, "records": n, "errors": n, "warnings": n, "baseline": "v1" | None, "clean": bool}

def build_todo(root: Path) -> Todo
def render_todo_text(t: Todo) -> str
def render_todo_json(t: Todo) -> str   # sort_keys, indent=2, trailing newline
```

**The `source.not_ingested` gate.** A repo file (`gitfiles.repo_files`) that matches any `config.sources[].paths`, has a reader (`kb.sources.readers.reader_for`, or whatever `kb ingest` uses to decide what it can read; reuse that, don't duplicate it), is not under `visibility.restricted`, and is not the `original` of any stored source gives:

`Issue("source.not_ingested", f"{path} is a document under config.sources that was never ingested; run `kb ingest {path}`", "warning", path=path)`

It sits in the source gate, so `kb check` shows it.

**`do` items, built in Task 1:**

| Condition | `id` | `code` | `command` |
|---|---|---|---|
| No `kb.config.yaml` | `init` | `kb.init` | `kb init`. This is the only item. `state.initialized` is false; return early. |
| Configured but no stored sources and documents present | `ingest:all` | `kb.ingest` | `kb ingest` |
| Each `source.not_ingested` | `ingest:<path>` | `kb.ingest` | `kb ingest <path>` |
| Any `source.outdated` | `sync` | `kb.sync` | `kb source sync`, with the message "documents changed since they were stored". One item, whatever the number. |
| Live records quoting text that changed between HEAD and the working tree (`kb.ingest.redraft.changed_segments(KbRepo.at_ref(root,"HEAD"), repo)` + `citing`), or with a quote not found (`unfound`) | `redraft:changed` | `kb.redraft` | `kb redraft --changed` |
| Untriaged segments with no live citer (`kb.ingest.redraft.new_text`) | `redraft:new` | `kb.redraft` | `kb redraft --new` |
| Every other `kb check` **error** except `config.owner_todo` and `source.secret_in_original` (those become `ask` items in Task 2) | `fix:<code>:<record or path>` | the issue code | None, with the issue message |
| `segment.untriaged` warnings, grouped into one item per source | `triage:<SRC>` | `segment.untriaged` | None, with "give every segment a triage entry" |

- `state` is computed from `run_check`.
- `clean` = no errors, no `do` items and no `ask` items.
- `baseline` = the highest existing `kb/baseline/vN.yaml` version, or None.

**CLI:** `kb todo [--format text|json]`. The text output has three parts: a "Do" list (each item `- <message>` plus `  run: <command>`), an "Ask the user" list (empty in Task 1), and one summary line `"<n> to do, <m> questions for the user"` on stderr.

- [ ] **Step 1: Write the failing tests.**
  - `test_todo_without_config_suggests_init`
  - `test_todo_new_repo_suggests_ingest`
  - `test_not_ingested_document_listed_and_warned`
  - `test_unreadable_file_not_reported`
  - `test_restricted_path_not_reported`
  - `test_changed_document_suggests_sync`
  - `test_changed_cited_text_suggests_redraft_changed`
  - `test_new_text_suggests_redraft_new`
  - `test_check_errors_become_fix_items`
  - `test_untriaged_grouped_per_source`
  - `test_todo_json_is_deterministic_and_sorted`
  - `test_clean_repo_is_clean`
- [ ] **Step 2: Run** `uv run pytest tests/test_todo.py tests/gates/test_source_not_ingested.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command, then `uv run pytest -q`. Expected: PASS. Existing tests may need the new warning added to their expectations. Change only those expectations.
- [ ] **Step 5: Commit** `feat(todo): kb todo lists agent work; source.not_ingested`.

### Task 2: `ask` items, the human decisions with evidence and options

**Files:**
- Modify: `src/kb/todo.py`
- Test: `tests/test_todo_ask.py`

**Ask items.** Each item's options are exactly as below; `{…}` are filled in.

- **`vague:<ID>`**: a live requirement whose `flags` include `vague`.
  - Question: `'{ID} says "{statement}" but gives no measurable target. What should it be?'`
  - Options:
    - `target`: "Give a measurable target (a number and a unit)". Then: "record the answer with `kb clarify`, then `kb redraft {ID} --new` and quote the new sentence".
    - `keep`: "Keep it flagged for now". Then: "leave it; it stays a warning".
    - `retire`: "It is not a real requirement". Then: "set `status: rejected` on {ID}; never delete the file".
- **`conflicting:<ID>`**: flag `conflicting`.
  - Question: `'{ID} contradicts another requirement. Which document is right?'`
  - Options:
    - `decide`: "Say which is right". Then: "record it with `kb clarify`, update or retire the other requirement with `kb redraft`".
    - `keep`
    - `retire`
- **`untestable:<ID>`**: flag `untestable`.
  - Question: `'{ID} cannot be tested as written. How should it be checked?'`
  - Options:
    - `define`: "Describe how to check it". Then: "record it with `kb clarify`, then `kb redraft {ID} --new`".
    - `keep`
    - `retire`
- **`owner:<where>`**: from each `config.owner_todo` issue, where `<where>` is the issue's location, for example `policy.approvers.intent` or `modules.MOD-billing.owner`.
  - Question for intent: `"Who approves changes to requirements? Give a forge handle such as @org/team or @user."`
  - Question for decisions: `"Who approves decisions and invariants? Give a forge handle such as @org/team or @user."`
  - Question for a module: `"Who owns {MOD} ({title})? Give a forge handle."`
  - Option:
    - `handle`: "Give a handle". Then: "write it into kb.config.yaml at {where}".
- **`secret:<SRC>`**: from `source.secret_in_original`.
  - Question: `"{document} contains a secret (the stored copy is masked). Remove it from the original?"`
  - Options:
    - `remove`: "Remove it from the document". Then: "delete that paragraph from {document} (or ask the person to, for PDF or Word), then `kb source sync`; tell the person to rotate the key".
    - `self`: "I will handle it". Then: "leave it; kb check keeps reporting it".
- **`freeze:v<N>`**: offered only when all of these hold: the repo is initialized, `run_check` has no errors, there are no `do` items, there are no other `ask` items, HEAD is the tip of the default branch (`gitfiles.find_default_branch`, `rev_parse`), and no `kb/baseline/v*.yaml` exists, or live requirements differ from the latest baseline file.
  - Question: `"The knowledge base has no errors. Freeze baseline v{N}?"` N is the next version.
  - Options:
    - `freeze`: "Freeze it". Then: "`kb baseline freeze v{N}`, commit, and open a PR for review".
    - `later`: "Not yet". Then: "leave it".

**Evidence** for record items:

```python
{"record": id, "statement": rec.statement, "source": q.source, "quote": q.text,
 "document": source.original, "page": seg.page, "heading": seg.heading}
```

It uses the first quote. A missing segment gives empty values, not a crash.

**Text rendering of an ask item:** `"<n>. <question>"`, then `"   evidence: <document>, page <page>, <heading>: <inline_code(quote)>"`, then `"   (a) <label>"`, `"   (b) <label>"` and so on. The question also goes through `inline_code` for its embedded statement.

- [ ] **Step 1: Write the failing tests.**
  - `test_vague_requirement_becomes_ask_with_three_options`
  - `test_conflicting_and_untestable_asks`
  - `test_owner_todo_asks_per_location`
  - `test_secret_ask_names_document`
  - `test_freeze_offered_only_when_everything_is_clean`
  - `test_freeze_next_version_after_v1`
  - `test_todo_text_escapes_evidence`
  - `test_ask_ids_stable_across_runs`
- [ ] **Step 2: Run** `uv run pytest tests/test_todo_ask.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command, then `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(todo): ask items with evidence and options`.

### Task 3: `kb clarify` records a person's answer as a quotable source

**Files:**
- Create: `src/kb/clarify.py`
- Modify: `src/kb/cli.py`, adding the `clarify` command.
- Test: `tests/test_clarify.py`

**Interface:**

```python
CLARIFICATIONS = "docs/clarifications.md"
class ClarifyError(ValueError): ...
def clarify(root: Path, topic: str, text: str, by: str, date: str) -> tuple[str, str]   # (source id, "SRC-x#seg-NN" of the new section)
```

**Format.** When the file does not exist, create it with:

```markdown
# Clarifications

Answers to open questions, recorded by the coding agent from the decision-maker's reply. Each section can be quoted as evidence.
```

Append, separated by a blank line:

```markdown
## {topic}

Decided by {by} on {date}.

{text}
```

**Validation:**
- `topic` and `text` are non-empty after strip.
- Neither contains a newline. `text` must be one paragraph, so the CLI joins wrapped input itself.
- `topic` does not start with `#`.
- `by` matches `^@[\w.-]+(/[\w.-]+)?$`.
- `date` matches `^\d{4}-\d{2}-\d{2}$`.
- `docs/clarifications.md` must match `config.sources`, else ClarifyError "add docs/** (or docs/clarifications.md) to config.sources".

Any violation raises ClarifyError, which exits 2.

**Storing.** After writing, call `kb.sources.store.add_source(root, CLARIFICATIONS, config)`, which creates the source or re-syncs it. Return the ref of the segment whose heading is the new topic: the last segment with that heading.

**CLI:** `kb clarify --topic TEXT --text TEXT --by @handle [--date YYYY-MM-DD]`. The date defaults to today, local time. Print `"recorded in docs/clarifications.md; quote it as <ref>"` to stdout.

- [ ] **Step 1: Write the failing tests.**
  - `test_clarify_creates_file_and_source`
  - `test_clarify_appends_and_keeps_old_segment_ids`
  - `test_clarify_returns_ref_of_new_section`
  - `test_clarify_refuses_headings_and_blank_lines`
  - `test_clarify_refuses_bad_handle_and_date`
  - `test_clarify_outside_sources_exits_2`
  - `test_clarified_target_clears_vague_via_redraft`: after `kb clarify` with "The checkout page must load in under 2 seconds for 95% of visits.", run `kb redraft REQ-NF-001 --new`; the test rewrites REQ-NF-001 to quote the new segment, drops `vague` and triages the segment; `kb redraft --report --strict` exits 0.
- [ ] **Step 2: Run** `uv run pytest tests/test_clarify.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command, then `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(clarify): record a person's answer as quotable evidence`.

### Task 4: The agent playbook, the session-start summary, and A-18

**Files:**
- Modify:
  - `src/kb/rules.py`: add rule 6.
  - `src/kb/init/templates/agents_block.md`: add the playbook section.
  - `src/kb/agents/claude_code.py`: install the skill.
  - `src/kb/hooks.py`: add the summary.
  - `kb_spec.md`: add A-18.
- Create: `src/kb/init/templates/skill_kb.md` (the Claude Code skill)
- Test: `tests/agents/test_playbook.py`, plus new cases in `tests/agents/test_hooks.py`

**Rule 6**, exact text:

`"6. Drive the knowledge base yourself: run `kb todo`, do every item under Do, and ask the user every item under Ask the user, offering its options. Record each answer with `kb clarify`, then follow up as the option says. Never invent a target, quote or approval."`

**AGENTS.md playbook section.** It is appended to `agents_block.md` after `{{rules}}`, inside the same kb block, under `## Working with the knowledge base`. Steps, exact text:
1. When the user asks you to set up or update the knowledge base, or when the session brief says there is kb work, run `kb todo --format json`.
2. Do every `do` item, using its `command` when it has one. For a brief (`.kb/cache/ingest/brief.md` or `.kb/cache/redraft/brief.md`), follow it, then run `kb ingest --report` or `kb redraft --report` and fix what they reject.
3. Collect every `ask` item. Ask the user all of them in one message, numbered, showing the evidence and the lettered options. Accept short answers like "1a, 2b".
4. For each answer, do what its option's `then` says. If the user gives a new fact (a target, a handle, which document is right), record it first: `kb clarify --topic "<short topic>" --text "<the user's answer as one sentence>" --by <the user's handle>`.
5. Run `kb todo` again and repeat until it is clean, then run `kb check --strict`.
6. Before the baseline exists, work on the current branch. After it, make changes on a branch and open a pull request; never merge it yourself.

**The skill** `skill_kb.md` has this frontmatter:

```yaml
---
name: kb
description: Set up or update the product knowledge base (kb) from documents, resolve its to-do list and ask the user only for decisions. Use when the user mentions requirements, the knowledge base, kb, documents in docs/, or a changed or new document.
---
```

The body is the same playbook (keep one source of truth: render both from one template string in `src/kb/rules.py`, `PLAYBOOK: list[str]`, rather than duplicating it), plus a short example exchange.

`ClaudeCode.install` also writes `.claude/skills/kb/SKILL.md` through the existing symlink-safe writer (`kb.safewrite`). An existing file is overwritten only when it carries kb's marker comment `<!-- written by kb -->`; otherwise refuse with a message. `kb init` keeps writing AGENTS.md as today, now including the playbook.

**Session-start summary.** In `hooks._brief`, when `build_todo(root)` has `do` or `ask` items, prepend:

`"## kb to-do\n<n> things to do, <m> questions for the user. Run `kb todo` and follow the playbook in AGENTS.md.\n"`

Swallow any exception from `build_todo`, so the existing brief always prints.

**A-18 · Agent-driven mode** goes in `kb_spec.md` §0 after A-17. Its points:
- the user talks to their coding agent;
- `kb todo` separates agent work from human decisions, with options;
- `kb clarify` turns an answer into a quotable source (`docs/clarifications.md`, trust as configured);
- agents never answer `ask` items, invent targets or approve;
- after the baseline, changes go through a PR;
- new documents under config.sources are detected (`source.not_ingested`).

- [ ] **Step 1: Write the failing tests.**
  - `test_rules_include_rule_6`
  - `test_agents_block_contains_playbook`
  - `test_claude_code_install_writes_skill`
  - `test_skill_not_overwritten_without_marker`
  - `test_session_start_prepends_todo_summary`
  - `test_session_start_survives_todo_error`
  - `test_playbook_single_source` (the AGENTS block and the skill body both contain every PLAYBOOK line)
- [ ] **Step 2: Run** `uv run pytest tests/agents -q`. Expected: FAIL.
- [ ] **Step 3: Implement**, then write A-18.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Update existing tests that assert the exact rules or AGENTS.md text only where the new content is added.
- [ ] **Step 5: Commit** `feat(agents): playbook, kb skill and to-do summary; A-18`.

### Task 5: Agent-driven acceptance, docs and demo

**Files:**
- Create: `tests/e2e/test_agent_mode.py`, `docs/phases/agent-mode-demo.md`
- Modify: `README.md` (an "Agent-driven mode" section and the `kb todo` / `kb clarify` rows), `src/kb/init/templates/kb_readme.md` (a short section)

**The e2e test `test_agent_driven_setup_from_docs`** is a scripted "agent" that only runs the commands the playbook names and writes the files an agent would:
1. Start from a docs-only repo with a Markdown spec containing a vague line ("The checkout page must be fast.").
2. `kb todo` gives `init`.
3. `kb init` and commit. `kb todo` gives `ingest:all` plus two `owner:` asks.
4. `kb ingest` and the scripted drafts, including REQ-NF-001 flagged vague, and triage. Then `kb ingest --report --strict` exits 0. Commit.
5. `kb todo` asks `vague:REQ-NF-001` and the owners.
6. Answer: write the approvers into the config; `kb clarify` the target; `kb redraft REQ-NF-001 --new`; the scripted redraft; `kb redraft --report --strict` exits 0. Commit.
7. `kb todo` asks only `freeze:v1`.
8. `kb baseline freeze v1`, commit. `kb todo` is clean.

**A second test, `test_new_document_after_freeze_is_detected`:** add `docs/refund-policy.md`. `kb todo` gives `ingest:docs/refund-policy.md`, and `kb check` warns `source.not_ingested`.

**`docs/phases/agent-mode-demo.md`** is the manual walkthrough on `/Users/dev/projects/acme-billing`. It covers installing 0.3.0, `kb init` and `kb agents install claude-code`, then one sentence to the agent, then answering its questions. It includes tables to fill in.

- [ ] **Step 1: Write** the e2e tests.
- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_agent_mode.py -q`. Fix gaps in the owning module, with a unit test each.
- [ ] **Step 3: Write** the docs.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `test(e2e): agent-driven setup and new-document detection; docs`.
