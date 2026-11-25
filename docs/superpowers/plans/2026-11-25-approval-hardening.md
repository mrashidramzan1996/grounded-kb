# Approval Hardening (0.3.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three approval holes an external review found: a pull request can rewrite its own approval policy, an unreadable team approves anyone, and features/glossary/components change with no approval.

**Architecture:** `kb verify` takes its *policy* (approvers, teams, bots, fail_on, mode, module owners, CODEOWNERS) from the merge-base whenever there is a base, never from the change under review, and any change to `kb.config.yaml` or a CODEOWNERS file needs the decision approvers. Team approvals fail closed unless membership is readable from the forge or listed in the base config. Every record type is routed to an approver group, and numbers in feature claims must come from the linked requirement.

**Tech Stack:** Python ≥3.12, uv, Typer, pydantic v2, pytest. Existing modules: `src/kb/verify/{run,approvals,report}.py`, `src/kb/config/{schema,load,validate}.py`, `src/kb/gates/quotes.py`, `src/kb/init/scaffold.py`, `src/kb/forge/{local,github}.py`, `src/kb/gitfiles.py`.

**Spec:** No separate spec. The authority is the review feedback items #1, #2 and #5 as evaluated in this session:
- #1 "kb verify reads approvers, fail_on, mode and CODEOWNERS from the PR branch … Fix: read policy and CODEOWNERS from the merge-base, and treat any change to them as needing decision approval."
- #2 "If team membership can't be read, any approval counts … Fix: fail closed, or resolve teams to logins in the base config."
- #5 "Features and glossary terms have no quotes and need no approval … Fix: route kb/features/ and kb/glossary/ to intent approvers, and number-check claim text."

## Global Constraints

- Policy is read before the project's tests run (the existing snapshot rule in `run_verify`: everything read from the tree or the forge comes first).
- A base that cannot be read (bad YAML, git error) is an error with exit 2, never a silent fallback to the working tree.
- Fail closed: when kb cannot prove an approval, it reports `missing_owner_approval` (category `missing_owner_approval`, severity error).
- No document-controlled or PR-controlled text in commands kb prints; handles and paths go through `inline_code` / `_md` as today.
- Existing behaviour without a base (`kb verify --all`, no default branch) is unchanged: policy comes from the working tree.
- Commits end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01FU26Rg1DsNpsAtdAuPuQzD`

## Review Focus

1. Shallow CI checkout, or a base whose `kb.config.yaml` is invalid YAML: verify must exit 2 with the existing fetch-depth hint / a ConfigError naming the ref, never use the PR's config. (Task 1 test `test_invalid_base_config_is_an_error`.)
2. A CODEOWNERS entry that is a symlink at the base (git mode 120000) must not be read as rules. (Task 1 test `test_codeowners_symlink_at_base_ignored`.)
3. A PR that moves CODEOWNERS between locations (`.github/CODEOWNERS` → `CODEOWNERS`) counts as a CODEOWNERS change. (Task 1 test `test_moving_codeowners_needs_decision_approval`.)
4. Team handle case: `@Acme/Product` in approvers vs `@acme/product` as `git_team` must resolve to the same configured members. (Task 2 test `test_configured_members_match_case_insensitively`.)
5. A PR that introduces kb (no `kb.config.yaml` at the base): policy from the working tree, and the Approvals section says so. (Task 1 test `test_config_new_in_this_change_uses_working_tree_and_says_so`.)

---

### Task 1: Policy from the merge-base; config and CODEOWNERS changes need decision approvers

**Files:**
- Create: `src/kb/verify/policy.py`
- Modify: `src/kb/config/load.py` (add `config_at`), `src/kb/ingest/report.py:59-66` (`base_config` becomes a thin call to `config_at`), `src/kb/verify/approvals.py` (`codeowners_rules_at`, config/CODEOWNERS rule in `required_owners`), `src/kb/verify/run.py:60-105`, `src/kb/verify/report.py` (Approvals section names the policy source; `VerifyReport.policy_source: str | None = None`), `README.md` (approvals section)
- Test: `tests/verify/test_policy.py` (new), `tests/verify/test_approvals.py`, `tests/e2e/test_phase2.py` (one scenario)

**Interfaces:**
- Produces:
  - `kb.config.load.config_at(root: Path, ref: str) -> KbConfig | None`: None when `kb.config.yaml` is not in that tree; `ConfigError` naming the ref when it does not parse or validate.
  - `kb.verify.approvals.codeowners_rules_at(root: Path, ref: str) -> CodeownersRules | None`: same lookup order as `codeowners_rules` (`_CODEOWNERS`), reading blobs at `ref`; entries whose tree mode is a symlink (`tree_symlinks`) are skipped.
  - `kb.verify.policy.Policy` (frozen dataclass): `config: KbConfig`, `codeowners: CodeownersRules | None`, `source: str`.
  - `kb.verify.policy.load_policy(root: Path, base_ref: str | None, head_config: KbConfig) -> Policy`:
    - `base_ref` None → `Policy(head_config, codeowners_rules(root), "working tree")`.
    - base has no `kb.config.yaml` → `Policy(head_config, codeowners_rules_at(root, base_ref), "working tree (kb.config.yaml is new in this change)")`.
    - otherwise → `Policy(config_at(...), codeowners_rules_at(...), f"base {base_ref[:7]}")`.
  - `required_owners(..., policy_config: KbConfig, ...)`: approvers, teams, bots and module owners come from the policy config. The module list for routing changed files uses the policy config's modules, so a module added in the change does not own anything yet.
- Consumes: `kb.gitfiles.read_tree_files`, `file_at`, `tree_symlinks`; `kb.config.schema.KbConfig`.

- [ ] **Step 1: Write the failing tests** in `tests/verify/test_policy.py`, using the existing git-repo fixtures pattern from `tests/verify/test_approvals.py`:
  - `test_policy_comes_from_the_base`: base config has `intent: ["@acme/product"]`, `fail_on` default, `mode: enforce`; the head commit sets `intent: []`, `fail_on: []`, `mode: warn-only`. `load_policy(root, base_sha, head_cfg).config.policy` equals the base's; `source == f"base {base_sha[:7]}"`.
  - `test_no_base_uses_the_working_tree`: `load_policy(root, None, head_cfg).config is head_cfg`, `source == "working tree"`.
  - `test_config_new_in_this_change_uses_working_tree_and_says_so`: base has no `kb.config.yaml`; `source == "working tree (kb.config.yaml is new in this change)"`.
  - `test_invalid_base_config_is_an_error`: base `kb.config.yaml` is `version: [`; `pytest.raises(ConfigError)` and the message contains the ref.
  - `test_codeowners_from_the_base`: base CODEOWNERS `/kb/requirements/ @acme/product`; head deletes it; `load_policy(...).codeowners` still has that rule.
  - `test_codeowners_symlink_at_base_ignored`: base `CODEOWNERS` is a symlink to `docs/x`; `.github/CODEOWNERS` is a regular file; rules come from `.github/CODEOWNERS`.
- [ ] **Step 2: Write the failing tests** in `tests/verify/test_approvals.py`:
  - `test_config_change_needs_decision_approvers`: `changes.files` contains `kb.config.yaml`; with policy decisions `["@acme/tech-leads"]`, `required_owners(...)["@acme/tech-leads"]` contains `"kb.config.yaml changed"`.
  - `test_codeowners_change_needs_decision_approvers`: same for `.github/CODEOWNERS`, reason `".github/CODEOWNERS changed"`.
  - `test_moving_codeowners_needs_decision_approval`: changes contain both `CODEOWNERS` and `.github/CODEOWNERS`; both reasons present.
  - `test_config_change_with_no_base_decision_approvers_fails_closed`: policy decisions empty (`["TODO"]`); `required_owners` returns `{"(no decision approvers)": ["kb.config.yaml changed"]}`. `owner_approval` then reports `missing_owner_approval` with message `kb.config.yaml changed, but the base branch names no decision approvers; set policy.approvers.decisions on the default branch first`.
  - `test_emptied_intent_in_the_change_still_needs_base_intent_approvers`: policy config intent `["@acme/product"]`, a changed requirement → `"@acme/product"` required.
  - `test_module_added_in_the_change_owns_nothing_yet`: the head config adds `MOD-x` with owner `@attacker` and paths `src/**`; the policy (base) config has none; a changed `src/a.py` maps to no `@attacker` requirement.
- [ ] **Step 3: Run them and see them fail.** Run: `uv run pytest tests/verify/test_policy.py tests/verify/test_approvals.py -q`. Expected: ImportError / assertion failures.
- [ ] **Step 4: Implement** `config_at`, `codeowners_rules_at`, `Policy`/`load_policy`, and the config/CODEOWNERS rule in `required_owners`. The sentinel `"(no decision approvers)"` is the handle; `owner_approval` maps it to the message above, and it never becomes a login. In `run_verify`, call `load_policy(root, base_ref, config)` before tests run, next to the existing pre-test reads. Use `policy.config` for `required_owners`, `owner_approval` (bots and teams), `blocking_issues(..., policy.config.policy.fail_on)` and `mode=policy.config.policy.mode`. Use `policy.codeowners` instead of `codeowners_rules(root)`. Set `VerifyReport.policy_source = policy.source`. In the Markdown Approvals section, add a first line `Policy from <source>.`. In the text format, print `approvals: policy from <source>` when a PR is checked. Leave everything else (modules for tests/anchors, presets) on the working-tree config.
- [ ] **Step 5: Write the e2e scenario** in `tests/e2e/test_phase2.py`, `test_pr_cannot_loosen_its_own_policy`, with the local forge file pattern already used there. The base has approvers and a CODEOWNERS block. The PR empties `intent`, deletes the CODEOWNERS lines and changes REQ text, with no approvals. `kb verify --base <base> --forge local:<file>` exits 1, and its Markdown output lists `@acme/product` (requirement changed) and the decision approvers (kb.config.yaml changed, CODEOWNERS changed) under Approvals. After the local forge records approvals from members of both groups on the head SHA, it exits 0.
- [ ] **Step 6: Run** `uv run pytest tests/verify tests/e2e/test_phase2.py -q`. Expected: all pass.
- [ ] **Step 7: Update README** (approvals section): policy comes from the base branch; changes to `kb.config.yaml`/CODEOWNERS need the decision approvers; how to bootstrap (set approvers on the default branch first).
- [ ] **Step 8: Commit** `fix(verify): take approval policy and CODEOWNERS from the merge-base; config changes need decision approvers`.

### Task 2: Team approvals fail closed

**Files:**
- Modify: `src/kb/config/schema.py:16-17` (`Team.members`), `src/kb/config/schema.py:96-100` (`PolicyCfg.unreadable_teams`), `src/kb/verify/approvals.py:142-172` (`owner_approval`), `src/kb/init/scaffold.py` (config template comment for `teams`), `README.md`
- Test: `tests/verify/test_approvals.py`, `tests/config/` (schema test, wherever `Team` is tested today)

**Interfaces:**
- Consumes: Task 1's `owner_approval(forge, pr, policy_config, required)`, where config is the policy config.
- Produces: `Team.members: list[str] = []` (forge logins, no `@`); `PolicyCfg.unreadable_teams: Literal["fail", "defer"] = "fail"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_unreadable_team_fails_closed_by_default`: `forge.team_members` returns None; an approval from `someone` exists; result has a `missing_owner_approval` error whose message contains `cannot read the members of @acme/product` and `teams.<name>.members`.
  - `test_configured_members_used_when_forge_cannot_read`: config `teams: {product: {git_team: "@acme/product", members: ["alice"]}}`; forge returns None; approval by `alice` → no issues; approval by `bob` only → `missing_owner_approval`.
  - `test_configured_members_match_case_insensitively`: `git_team: "@Acme/Product"`, required handle `@acme/product`, member `Alice` approved as `alice` → satisfied.
  - `test_forge_membership_wins_over_config`: forge returns `{"carol"}`, config lists `["alice"]`; `alice`'s approval does not satisfy, `carol`'s does.
  - `test_defer_keeps_the_old_behaviour`: `policy.unreadable_teams: defer`; forge None; some approval → only the `owner_approval.deferred` warning (existing message unchanged).
  - Rename/adjust the existing `test_team_membership_satisfied_and_unreadable_defers_with_warning` so it sets `unreadable_teams: defer`.
  - Schema: `Team(git_team="@a/b", members=["x"])` is valid; `members` entries starting with `@` are rejected (`ValueError` mentioning "login without @").
- [ ] **Step 2: Run them and see them fail.** Run: `uv run pytest tests/verify/test_approvals.py -q`.
- [ ] **Step 3: Implement.** Members come from the forge; when it returns None, use the union of `members` of every policy-config team whose `git_team` equals the handle case-insensitively, if non-empty. Otherwise: `defer` and some approval → the existing deferred warning; else `missing_owner_approval` with `"{handle} has not approved {sha7}: cannot read the members of {handle}; list them under teams.<name>.members in kb.config.yaml on the base branch, or give the CI token read access to the organisation's teams ({why})"`.
- [ ] **Step 4: Run** `uv run pytest tests/verify tests/config -q`. Expected: all pass.
- [ ] **Step 5: Update** the `kb init` config template comment for `teams` and the README to show `members`.
- [ ] **Step 6: Commit** `fix(verify): team approvals fail closed unless membership is readable or listed in the base config`.

### Task 3: Every record type needs an approver; numbers in feature claims come from their requirement

**Files:**
- Modify: `src/kb/verify/approvals.py:97-110` (routing), `src/kb/init/scaffold.py:22` (`_APPROVAL_PATHS`), `src/kb/gates/quotes.py` (new feature check, or a sibling gate registered where `quotes` is), `src/kb/init/templates/kb_readme.md` (approvals and numbers paragraphs), `README.md`
- Test: `tests/verify/test_approvals.py`, `tests/gates/test_quotes.py` (or wherever the number check is tested), `tests/init/` scaffold test for the CODEOWNERS block

**Interfaces:**
- Consumes: `kb.gates.quotes.numbers(text: str) -> set[str]`; Task 1's `required_owners` signature.
- Produces: issue code `claim.number_not_in_requirement` (severity warning, category schema).

- [ ] **Step 1: Write the failing tests:**
  - `test_feature_change_requires_intent_approvers`: a changed `kb/features/FEAT-001.md` → intent approver reason `"feature FEAT-001 changed"`.
  - `test_glossary_change_requires_intent_approvers`: `kb/glossary/TERM-001.yaml` → `"glossary term TERM-001 changed"`.
  - `test_component_change_requires_decision_approvers`: `kb/components/COMP-001.md` → decisions approvers, `"component COMP-001 changed"`.
  - `test_codeowners_block_covers_every_record_dir`: `kb init` writes commented lines for `/kb/requirements/`, `/kb/features/`, `/kb/glossary/`, `/kb/decisions/`, `/kb/invariants/`, `/kb/components/`, `/kb.config.yaml`.
  - `test_claim_number_must_come_from_its_requirement`: REQ-F-001 statement "Seats can be removed until 500 seats." (quote has 500); FEAT-001 claim C1 `requirement: REQ-F-001`, claim text "Seats are free for the first 90 days." → `claim.number_not_in_requirement` warning, message `FEAT-001.C1: claim numbers not in REQ-F-001: 90`.
  - `test_claim_number_found_in_requirement_quote_passes`: claim says "500 seats" → no issue.
  - `test_claim_without_requirement_uses_the_feature_requirements`: claim has no `requirement`; feature `requirements: [REQ-F-001]`; "90" → warning naming `REQ-F-001`; a feature with no requirements and a numeric claim → warning `FEAT-001.C1: claim numbers not in any linked requirement: 90`.
  - `test_feature_body_numbers_checked`: feature body says "first 90 days" → warning `FEAT-001: title and body numbers not in any linked requirement: 90`.
  - IDs inside claims (`REQ-F-001`, `seg-03`) are not numbers (existing `_ID_LIKE` behaviour); assert no issue for "implements REQ-F-001".
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** the routing (requirements, features and glossary → intent; decisions, invariants and components → decisions), `_APPROVAL_PATHS`, and the number check. Only live features are checked (`live_ids`). Grounded numbers are `numbers()` over the linked requirements' statements plus their quote texts. Linked requirements are the claim's `requirement` if set, else the feature's `requirements`; for title and body, all of the feature's `requirements` plus every claim's `requirement`.
- [ ] **Step 4: Run** `uv run pytest tests/verify tests/gates tests/init -q`. Expected: all pass.
- [ ] **Step 5: Update** `kb_readme.md` and `README.md`: which approver group covers which record directory, and the feature number rule.
- [ ] **Step 6: Commit** `fix: route features, glossary and components to approvers; numbers in feature claims come from their requirements`.

### Task 4: Release prep 0.3.1

**Files:** `pyproject.toml` (version), `README.md` and `src/kb/init/templates/*` pins `grounded-kb==0.3.1` / `@v0.3.1` (same places 0.3.0 was bumped in commit 2104821), `uv.lock` if it records the version.

- [ ] **Step 1:** Bump every place `git show 2104821 --stat` touched from 0.3.0 to 0.3.1.
- [ ] **Step 2:** Run the full suite: `uv run pytest -q`. Expected: all pass. Run `uv run ruff check src`. Expected: clean.
- [ ] **Step 3: Commit** `release prep: version 0.3.1 (approval hardening)`.
