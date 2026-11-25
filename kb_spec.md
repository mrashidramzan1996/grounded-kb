# Grounded KB Framework — Specification and Build Context

> **For Claude Code (or any coding agent) reading this file.** This is the full design of a system we want to build. Treat it as the source of truth for requirements and design decisions. Status tags: **[designed]** = decided, build it as written; **[planned]** = agreed direction, build after the MVP; **[open]** = not decided, ask before building. Example numbers, IDs, team names and file names (acme-billing, REQ-F-034, ADR-0041, "lee", "dana") are illustrative. Do not invent features that are not here; if something is missing or contradictory, stop and ask. Build in the milestone order in §20.

Version: 2026-11-23 (rev. 2: §0 Amendments) · Owner: the person who handed you this file

---

## Contents

0. Amendments (2026-11-23, after plan review)
1. Problem and goal
2. The rules (non-negotiable)
3. Scope: software profile first, generic core
4. Concepts and glossary
5. Architecture: 38 components
6. Phase 1 · Baseline from requirement documents
7. Phase 2 · Verified docs against code and tests
8. Phase 3 · Agent context
9. Phase 4 · Multi-agent coordination
10. Record types and schemas
11. Segments, claims and quotes
12. Evidence levels, fingerprints and staleness
13. Test coverage strategy
14. Storage layout
15. `kb.config.yaml`
16. Commands
17. Agent integration (Claude Code, other agents, MCP)
18. CI gate and enforcement rules
19. Security and trust boundary
20. Build plan, tech stack and milestones
21. Entity-driven extension [planned]
22. Lessons from research and the market
23. Open questions
24. Appendix: worked examples

---

## 0. Amendments (2026-11-23, after plan review) [designed]

These come out of three independent reviews of the implementation plan: simplicity, approach and feasibility. They **override the sections they name**, and the original text below is kept unchanged so its history stays readable. The implementation plan is `docs/superpowers/plans/2026-11-23-grounded-kb.md`.

- **A-1 · Verification state is derived, not stored** (§5 fingerprint store, §10 feature claim, §12).
  - Record files hold only human-owned content: statement, quotes, anchor, test link, links, decisions.
  - `verified / stale / broken / unverified`, the evidence level, `judged_by`, fingerprints and `confirmed_at` are computed by `kb verify` / `kb status`. The computation compares the merge-base with head and uses test runs.
  - They are never committed. Records that contain these fields are rejected.
  - Why: committing them means bot commits on every PR, which don't re-trigger CI and cause merge conflicts between parallel PRs.
- **A-2 · Approval is a merged PR; git is the audit log** (§2.2, §10 provenance and audit log, §14 `kb/audit/`).
  - A record on the default branch is approved or accepted if it merged through a PR with the required owner reviews: CODEOWNERS plus the `missing_owner_approval` rule.
  - A record that exists only on an unmerged branch is proposed.
  - Stored `status` is only `live` or `rejected`.
  - `superseded` is derived from a live record's `supersedes` field, so the superseded record is never edited.
  - Proposer, approver and timestamps come from git and the forge (`kb audit`). Model or agent drafting provenance is stored as `drafted_by`.
  - `kb/audit/*.jsonl`, `approved_by` and `proposed_by` are removed.
- **A-3 · Only deterministic gates block a PR** (§5 model components, §12, §18).
  - Model steps are advisory: E4 judging, re-confirmation, divergence checks and extraction in CI.
  - Mutation testing (E5) runs nightly or on a label, through existing mutation tools.
  - A PR blocks only when a claim falls below E2 (anchor resolves and linked test passes). The deeper required levels (`standard → E4`, `high → E5`) are enforced by the coverage ratchet.
- **A-4 · Staleness rule** (§12 decision order).
  - A claim is verified while its anchor resolves and its linked tests pass. A changed fingerprint alone adds a "changed" note and an optional advisory re-confirm.
  - A requirement is stale when the text of its cited segment changes. It is broken when its quote is no longer found.
  - An anchor that can't be resolved because no adapter exists, or because the code is dynamic, is `unverified`, not `broken`.
- **A-5 · Phase 4 is git-native first** (§9).
  - **Stale context:** a PR fails if any record governing its changed files changed on the default branch after its merge-base.
  - **Change requests:** PRs carrying a superseding record, labelled `kb-proposal`.
  - **Pending notices:** these proposals are listed in agents' context.
  - **Accepted-change notices:** posted on affected open PRs.
  - **Overlap:** shown from open PRs.
  - The live service in §9 (registry, leases, event bus, revalidator, broadcast) is built only if measured need appears.
  - §23 item 7 (auto-applying `kb ask` answers) is declined.
- **A-6 · Access control** (§2.7, §19).
  - Content in a repository is protected by the repository's own permissions. Restricted sources live in a separate repository with its own access.
  - The access filter, applied before ranking, covers every **served** read path (MCP over HTTP, REST, hosted indexes), where the requester is authenticated.
- **A-7 · Model gateway providers** (§5, §20). There are five:
  - `agent`: the developer's running coding agent drafts records, and the gates check them. No API key needed.
  - `anthropic` (default API).
  - `openai-compatible`.
  - `agent-cli`: a headless agent with tools disabled and a read-only sandbox.
  - `replay`: recorded responses, for CI.
- **A-8 · Markers** (§13). Code and tests link to records with Tracey-compatible markers, `r[impl ID]` and `r[verify ID]`, in any comment syntax. `@claim("ID")` remains as Python sugar.
- **A-9 · Modules and owners** (§10, §15).
  - Modules are defined in `kb.config.yaml` only; MOD pages are generated.
  - `owner` may be a `teams` key or a forge handle (`@org/team`).
  - `kb init` merges a marked block into an existing CODEOWNERS file rather than generating the whole file. How modules are created is set by A-16.
  - A file in no module is a warning. A file in two modules is an error.
  - New config keys: `ignore`, `generated`, `bots` (default `["*[bot]"]`) and per-module `tests`.
- **A-10 · Requirement links** (§6.6). A requirement must link to at least one feature **or** module, since features may not exist yet at ingest.
- **A-11 · Generated outputs and the KB revision** (§4, §9, §14).
  - `kb/_generated/` is not committed. It is built locally into `.kb/cache/` or as a CI artifact.
  - The "KB revision" is the default branch's commit SHA.
- **A-12 · Agent rules files** (§17).
  - The rules live once, in an `AGENTS.md` block.
  - `CLAUDE.md` imports them with `@AGENTS.md`.
  - The rules are cut to four: use the injected context, cite IDs, never act against a live ADR or INV (supersede it instead), and finish with `kb check --changed`.
  - Hooks call the CLI and take changed files from git, not from hook payloads.
- **A-13 · Segment identity** (§11). Segment IDs stay stable across source refreshes. An unchanged segment (same hash) keeps its ID first. Remaining segments are then matched by heading path and occurrence, so an edited segment keeps its ID. New segments get new numbers, and numbers are never reused.
- **A-14 · Invariants** are stored one per file in `kb/invariants/INV-*.md` (§10, §14).
- **A-15 · Delivery order** (§20 milestones). The work is delivered in nine phases, each ending with a usable output, as set out in the plan. All spec components stay in scope.
- **A-16 · Starting from documents only** (§6, §15, §16, overriding the owner-prefill part of A-9).
  - **The starting point is a repo with only `docs/`** (PDF, Word, Markdown) and no code. Phase 1 therefore reads PDF and Word.
  - **Modules are never guessed** from folders. They come from evidence:
    - CODEOWNERS directory rules at `kb init`;
    - modules the drafting agent proposes from the documents (`kb module add`, without paths);
    - a person adding them.
  - **A module may have no paths.** Paths are suggested later from the files that carry `r[impl ID]` markers for the module's requirements, and are written only when someone runs `kb module paths MOD-x --accept`.
  - **Context works before any code exists**, by module, feature or ID. For new files, it uses the markers in them.
  - **Decisions and invariants drafted from documents** carry quote evidence.
  - **Requirements may carry `flags`** (vague, conflicting, untestable).
  - **A number in a statement that appears in none of its quotes** is flagged for reviewers.
  - **Every segment is triaged** in `triage.yaml`, either to the IDs drafted from it or to "none: <reason>" (§11 coverage accounting).
  - **`kb status`** reports each requirement as not built, built (an `r[impl]` marker exists) or tested (an `r[verify]` marker or a test link also exists). Phase 2 adds verified, meaning the tests actually pass.
  - **`kb baseline freeze`** moves to Phase 1.
- **A-17 · Targeted redraft** (§6, §11, §12, §16).
  - **Only the affected records are redrafted.** When a cited source changes (`kb source sync` names the records citing changed text), or a requirement is flagged, `kb redraft` writes a brief for just those records. New document text is drafted with `kb redraft --new`: new records may quote only the new or changed segments. They go through the same brief, gates and review loop as `kb ingest`, and `kb redraft --report` gates the result.
  - **IDs are kept.** A redrafted requirement keeps its ID and is edited in place. A record whose text is gone gets `status: rejected`; it is never deleted or renamed.
  - **`vague` clears only with a measurable quote.** The flag may be removed only when a quote of the record states a measurable target (it contains a number). Removing any other flag is flagged for the reviewer.
  - **ADRs and INVs change only by superseding** (§2.4). A new record with `supersedes: <old ID>` takes the next free ID; the accepted one stays byte-identical.
- **A-18 · Agent-driven mode** (§2.2, §6, §8, §16).
  - **The user talks to their coding agent.** They do not run kb commands themselves. `kb init` puts a playbook in the kb block of `AGENTS.md`, and `kb agents install claude-code` also writes it as the `kb` skill (`.claude/skills/kb/SKILL.md`). The session-start hook says when there is kb work (`## kb to-do`).
  - **`kb todo` separates agent work from human decisions.** `do` items are for the agent, most with the command to run. `ask` items are for the person; each carries its evidence and lettered options, and each option says what the agent does next. `--format json` is deterministic and its ids are stable.
  - **`kb clarify` turns an answer into a quotable source.** It appends the person's answer to `docs/clarifications.md` and stores it as a source, so a redrafted requirement can quote it. Its trust is whatever `config.sources` gives that path.
  - **Agents never answer `ask` items, invent targets or approve.** They ask the person, record only what the person said, and follow the chosen option. Approval is still a merged PR (A-2).
  - **After the baseline, changes go through a PR.** Before it, the agent works on the current branch. After it, it works on a branch, opens a PR and never merges it.
  - **New documents are detected.** A readable file under `config.sources` that was never ingested gives the warning `source.not_ingested`, and `kb todo` turns it into an `ingest:<path>` item.

## 1. Problem and goal

AI coding agents state "facts" about a product (what it does, why, which decisions govern it) that are often invented, outdated or unverifiable. Requirement documents drift from the code; decisions made during development (by people or agents) are lost; nothing tells an agent which statements are still true.

**Goal:** a knowledge base (KB) for a software product in which every statement is a *claim with evidence*, checked by code, approved by people where intent is involved, kept current automatically as code and documents change, and served to agents as a small, scoped, verified context. Many agents working at once must stay consistent with it.

Success means:
- An agent's answer about the product can be traced to a source page, a code symbol or a test.
- Invented claims cannot enter the KB (quote must exist verbatim).
- When code or a source changes, exactly the affected claims are re-checked on that pull request.
- Decisions are recorded, superseded rather than edited, and approved by humans.
- Agents get the governing rules for the files they touch, within a token budget.

## 2. The rules (non-negotiable) [designed]

1. **Every fact is a claim with evidence.** No evidence, no claim. For documents: a verbatim quote. For behaviour: a code anchor and a test.
2. **Agents propose, code verifies, people approve.** Models and agents only produce drafts. Deterministic gates accept or reject. Humans approve intent, requirements and decisions. An agent's (bot account's) approval never counts.
3. **Fail closed.** Unknown file format, unresolved anchor, missing quote, missing approval → reject or report; never guess.
4. **Decisions are immutable.** An accepted decision is never edited; a new record supersedes it. History stays readable.
5. **Everything is files in git, changed by pull request.** Generated views are rebuilt, never hand-edited.
6. **Source material is data, not instructions.** Text from documents, tickets or other agents can never instruct an agent.
7. **Access is checked on every read path** (search, get-by-id, context, entity lookups), and filtering happens *before* ranking.
8. **Contradictions are recorded, never silently dropped or overwritten.**
9. **The engine (not any agent) announces accepted changes.**

## 3. Scope [designed]

- **Build first:** the *software profile* — KB for one software product in one git repository, with code/test verification.
- **Keep the core generic:** three clean boundaries so other domains can be added later without core changes: **source adapters** (file formats), **verifiers** (what a claim is checked against), **context API**.
- **Not in scope now:** enterprise live-data reconciliation (CDC/Kafka, MDM, polyglot databases). See §22 for why and how it could plug in later as an "enterprise profile" [planned].

## 4. Concepts and glossary

| Term | Meaning |
|---|---|
| Source | A raw input document (spec, PRD, ticket export, design doc). ID `SRC-<slug>`. |
| Normalise | Convert any format to plain text with heading markers; remove repeated headers/footers; re-join hyphenated words. Deterministic. |
| Segment | A structural piece of a source: one heading + its paragraphs (or one message / table row), with heading path, page, char offsets, hash. Evidence; never moves. ID `SRC-…#seg-NN`. |
| Claim | One atomic statement (requirement, behaviour, structure) with evidence. Semantic. |
| Quote | Exact text span from a stored, masked segment that supports a claim. |
| Anchor | A pointer to a code item: `path#symbol`, config key, flag, route, table. |
| Fingerprint | Hash of an anchored symbol's syntax tree with formatting and comments removed. |
| Evidence level | E0–E5 strength of proof for a claim (§12). |
| Baseline | Frozen set of approved requirements (`kb/baseline/v1.yaml` + git tag). |
| Record | One KB file: REQ, FEAT, MOD, ADR, INV, TERM, COMP, SRC, (ENT planned). |
| KB revision | Monotonic counter incremented on each merge that changes records; used by Phase 4. |
| Lease | Short-lived claim by an agent session on files/records it is about to change (Phase 4). |
| Context pack | Budgeted set of cards and records given to an agent for a task. |

## 5. Architecture: 38 components [designed]

Layers top to bottom. Kind: **det** = deterministic code, **model** = calls the model gateway (output is a draft and must pass gates), **human** = human decision, **input**.

### Inputs
| Component | Kind | Role |
|---|---|---|
| Code | input | Source at the commit being checked: code, config, flags, lockfiles, migrations. Read-only. |
| Tests | input | Unit/integration/contract/e2e tests; strongest evidence. |
| Raw sources | input | Specs, PRDs, tickets, design docs, PDFs. Untrusted until guarded. |
| kb.config.yaml | input | The only per-project settings file (§15). |

### Adapters
| Component | Kind | Role |
|---|---|---|
| Code adapters | det | Per language/framework: parse to syntax tree, list symbols (functions, classes, routes, tables, config keys, flags), fingerprint, resolve anchors, map imports between modules. |
| Test adapter | det | Run linked tests only; pass/fail; rerun to detect flaky; per-test coverage; mutation testing for high-risk claims. |
| Source adapters | det | Normalise any format, segment, keep offsets, hash source and segments. Unknown format → skip and report. |
| Model gateway | model | The only path to a model. Picks tier per job, fills versioned prompt with bounded input, requires schema-valid JSON, caches by input fingerprint, logs model+prompt version. Never writes to the store. |

### Trust boundary
| Ingestion guard | det | Mask secrets and personal data (placeholders like `[EMAIL-1]`, `[SECRET:stripe-test-key]`); if a secret is in the original file, open a security issue; flag instruction-like text ("ignore your instructions") and keep it as quoted data; attach visibility label. Positions/hashes computed on masked text. Runs before anything is stored or sent to a model. |

### Engine · 1 Detect
| Change detector | det | Diff since each claim's last confirmed commit: symbols, config keys, flags, lockfile packages, edited tests, changed source hashes, superseded decisions. |
| Scanner | det | Assign files to modules (config paths), list public interfaces, write structure claims (true by construction), one card per file. |
| Fingerprint store | det | Stored hash per anchor at confirmation; compare with current; test history. |
| Anchor index | det | Reverse lookup item → claims citing it; plus one dependency hop; lockfile package change = change to every importing module. |

### Engine · 2 Build + gate
| Generator | model | Structure claims from scanner; behaviour-claim drafts from one module's code+tests, each naming an anchor and a test. |
| Ingestor | model | From guarded segments: atomic requirements/claims with verbatim quotes; label intent/rationale/external fact/discussion; flag vague, conflicting, untestable; decision drafts from passages that record a choice. |
| Linker | model | Link drafts to requirements, modules, features, terms by name/alias; model only breaks ties; reject links to non-existent IDs. |
| Gates | det | Schema valid; anchors resolve; every quote found verbatim in stored text; linked tests exist; actor may set that status (identity from GitHub); on failure reject + log. Every draft and status change passes here. |

### Engine · 3 Check
| Staleness detector | det | Anchor gone → broken; fingerprint unchanged → verified; linked test fails → broken; evidence weakened → stale; code changed but tests pass at required level → re-confirmer. |
| Test verifier | model | Grade evidence E1–E5 (E4 labelled `judged_by: model`); quarantine flaky tests; flag tests edited together with their code (co-changed). |
| Divergence check | model | Find candidate pairs by code (same subject+predicate, feature, term), model judges only those: match / contradiction / not covered. |
| Re-confirmer | model | Claim + code diff + test results → still true / changed (draft edit) / unsure (human). |

### Engine · 4 Measure + route
| Coverage analyzer | det | Public interfaces without claims; requirements without features; claims without tests (claim coverage, §13); files in no module or two. |
| Review router | det | Map items to module and owning team; prioritise by risk; batch low risk; cap per PR; escalate old items. |
| Approval sampler | det | Random sample of approvals and auto-confirmations to a second reviewer; disagreement rate per module/prompt; switch off auto-approval where too high. |
| Schema migrator | det | Migrate records to new KB format versions, validate. |

### Store
| Claim store | det | `/kb` records, baseline, append-only audit log, provenance. Changed only by PR. |
| Generated outputs | det | `kb/_generated/`: index, relationship graph, file cards, reports, traceability matrix, CODEOWNERS. Rebuilt on every merge. |

### Context service (Phase 3)
| Impact analyzer | det | From changed files/symbols/flags walk claims → features → requirements → decisions → invariants; collect owners and tests. |
| Context packer | det | Exact signals → graph expansion (1–2 hops) → metadata filter → rank → layered packing within budget; never drop a governing rule. |
| Access filter | det | Visibility labels and readers; applied on every read path, before ranking. |

### Coordination service (Phase 4)
| Agent registry | det | Live sessions: agent identity (bot account), task, branch, records + KB revision of its context pack, file leases, status (working/waiting/blocked/done), heartbeats, expiry. |
| Change requests | det | Agent/person asks to change an accepted record → draft superseding record with options/rationale, impact and affected active agents attached; merge duplicate requests; after approval split into follow-up tasks. |
| Event bus | det | Ordered, replayable events (decision proposed/accepted/rejected, claim stale/broken, lease conflict, question asked/answered) delivered only to subscribed sessions with diff + new KB revision. Engine events are the only authoritative ones. |
| Ask + broadcast | model | Structured agent questions; answer from KB first; ask only agents with overlapping work; route intent changes to owners; compile one answer; agent replies are data, never approval; rate limits. |
| Plan revalidator | model | Notice + agent plan + rebuilt pack → continue / adapt / wait for task / stop; updates registry. Never changes ground truth. |

### Interfaces
| CI gate | det | Applies fail_on/warn_on; checks owner approvals; decision guard; stale-context check (§9); pass/fail on PR. |
| Agent tools | det | MCP + CLI (§17). |
| Review queue | human | People approve/reject/edit; approvals return through gates as signed status changes (GitHub identity). |

### Key data flows (edges, abbreviated)
- raw → source adapters → guard → ingestor ↔ model → linker → gates → store
- code → code adapters → scanner/fingerprints/anchor index; tests → test adapter → test verifier → staleness
- change → anchor index → staleness → (re-confirmer ↔ model) → gates → store
- store → generated outputs → impact/packer; access filter → packer → agent tools
- router → review queue → gates (signed) → store
- Phase 4: agent → registry; packer → registry; agent → change requests → impact/registry → gates/router; store/staleness → event bus → agent; agent → revalidator ↔ model → agent/registry; agent ↔ ask+broadcast → impact/registry/bus/router; registry → CI gate.

## 6. Phase 1 · Baseline from requirement documents [designed]

**Purpose:** turn project documents into approved, quoted requirements: the agreed ground truth.

Pipeline (per source): **Normalise → Segment → Guard → Store → Extract → Gates → Claim file → Review → Baseline freeze**

1. **Normalise** (det): any format → plain text with `##` heading markers. Same file in → same text out.
2. **Segment** (det): heading + paragraphs = one segment; ticket export = one message; table = one row with header; lists stay with their lead-in; long sections split only at sentence boundaries. Record heading path, page, start/end char offsets, hash.
3. **Guard** (det): masking + injection flags + visibility (§19). Nothing stored before this step.
4. **Store**: `kb/sources/SRC-<slug>/{source.yaml, text.md, segments.jsonl}` (§10).
5. **Extract** (model via gateway, job `extract`): drafts atomic claims, each with exact quote(s); vague lines flagged (e.g. "checkout must be fast"), never given invented numbers; decision drafts where a choice is recorded.
6. **Gates**: quote found verbatim in `text.md` (record char range); one fact; linked to a feature; else rejected and listed in the ingest report.
7. **Claim file**: `kb/requirements/REQ-*.yaml`, status `proposed`, `proposed_by: agent:ingestor (tier, prompt version)`.
8. **Review**: one PR, routed to `approvers.intent`; reviewers read claims next to quotes, may edit (e.g. "p95 < 300 ms"). Merge → `approved`, audit entry.
9. **Baseline**: `kb baseline freeze v1` → `kb/baseline/v1.yaml` (approved requirement IDs + hashes) + git tag `kb-baseline-v1`.

Hands over: approved baseline → Phase 2; contradictions with existing behaviour → feature owner.

## 7. Phase 2 · Verified docs [designed]

**Purpose:** document each module/feature and prove it against code, tests and the Phase 1 baseline, on every PR.

- `kb generate`: scan code (structure claims, file cards), draft behaviour claims via model, write `kb/features/FEAT-*.md`, `kb/modules/MOD-*.md`.
- **Three-way alignment** per requirement: baseline vs docs vs code+tests → `aligned` / `contradiction` (issue to owners; nobody auto-decides which side is right) / `not built`.
- Non-functional requirements: checked by load test results, monitoring or attestation (§13).
- Each behaviour claim: requirement link, code anchor, fingerprint, test, evidence level, `confirmed_at` commit.
- On every PR (`kb verify`): change detection → affected claims → run linked tests → grade evidence → staleness → re-confirm → gates → store → route approvals → CI result.
- Exit gate: every line of every approved requirement maps to a feature claim or an explicit "not built".

## 8. Phase 3 · Agent context [designed]

Retrieval order (graph-first, hybrid fallback):
1. **Exact signals** from the task: file paths, IDs, symbol names, glossary terms/aliases. No model.
2. **Graph expansion** 1–2 hops; always follow `governed_by`, `must_keep`, `implemented_by`. Governing decisions and invariants are never dropped.
3. **Metadata filter:** drop rejected/superseded/broken; keep stale but mark it; scope to modules/features; **access filter** (before ranking).
4. **Rank:** graph distance, then risk, then freshness.
5. **Pack in layers** within the token budget: L0 one-line cards for everything relevant; L1 verified claims + acceptance criteria of touched features/modules; L2 full governing decisions/invariants; rest as cards loadable via `kb_get(id)`.
6. **Fallback search** only when step 1 finds no exact signal: keyword (BM25) + vector over segments and cards, results merged; **every hit resolves to a record ID**, then steps 2–5. Vector index is a generated, rebuildable file in `kb/_generated/` (e.g. SQLite + vector extension), never served raw.

Every line of a pack carries its KB id so the agent can cite it.

## 9. Phase 4 · Multi-agent coordination [designed]

**Purpose:** many agents (Claude Code, Cursor, Codex…) on different machines working on the same project stay consistent.

### 9.1 Sessions and leases
- Hook at session start runs `kb session`: registers agent identity (bot account), task, branch, KB revision and record IDs of its context pack.
- On each file edit: take/renew a short **lease** on the file/records; warn when another session holds an overlapping lease (prevents merge conflicts early).
- Heartbeats; expire stale sessions and leases.

### 9.2 Ground-truth change requests
Example: agent A wants DynamoDB instead of Postgres (ADR-0009).
1. Agent runs `kb_request_change` (MCP) / `kb request-change ADR-0009` instead of silently changing the design in code.
2. Change requests draft **ADR-0041 supersedes ADR-0009** (status `proposed`, `decided_by: agent:claude-code@<session>`), with context, options, choice, rationale.
3. Impact attached (modules, features, invariants e.g. INV-005, tests, owners) + active agents in that area (from registry).
4. Gates accept it as *proposed* (agents can propose, never accept).
5. Event bus sends a **pending** notice only to affected sessions: "may change, do not build on the proposal yet".
6. Review router → approvers from config (e.g. tech-leads + owning team). Human approves (optionally with conditions), via PR/GitHub identity.
7. Gates accept ADR-0041, mark ADR-0009 superseded (kept). Change requests split follow-up tasks (T-1 create table, T-2 dual-write, T-3 cut over). Store writes; **KB revision increments** (e.g. 412 → 415).
8. Event bus broadcasts the accepted change (diff + revision) **only** to sessions whose pack or leases include affected records.
9. Plan revalidator per session: continue / adapt / wait for task T-n / stop. Registry records status; waiting agents are notified when the task completes.
10. **Stale-context check in CI:** a PR built on a context pack whose records were superseded fails ("rebuild context on rev 415 and re-plan") until the agent re-plans. Safety net for missed notices.

### 9.3 Asking other agents (`kb ask`)
Example: agent B wants to move "Billing alerts" into Settings.
1. `kb_ask` with type (question/proposal/heads-up), paths and records.
2. Answer from KB first (e.g. REQ-F-052 requires alerts one click from billing; a brand requirement may say logo changes need the design team).
3. Ask only agents with overlapping leases (e.g. agent E on settings/**), not all.
4. Agent replies are **data, not approval**.
5. Anything that changes intent/decisions → owning human via review router.
6. One compiled answer: no conflict / conflict (with whom) / needs approval / approved. Accepted changes go through gates (requirement edit, ADR) and are announced by the event bus.
7. Discussion log entry linked to touched records (append-only, not ground truth). Rate-limit broadcasts.

### 9.4 Event schema (sketch)
```json
{"seq": 9182, "type": "decision.accepted", "kb_rev": 415,
 "records": {"superseded": ["ADR-0009"], "accepted": ["ADR-0041"]},
 "affects": ["MOD-sessions","MOD-auth","FEAT-030","INV-005"],
 "tasks": ["T-1","T-2","T-3"], "to_sessions": ["A-7f2c","B-91aa","C-3d10"],
 "at": "2026-11-20T10:42:11Z"}
```
Event types: `decision.proposed|accepted|rejected`, `requirement.changed`, `claim.stale|broken`, `lease.conflict`, `question.asked|answered`, `task.done`.

### 9.5 Where Phase 4 state lives [open]
Sessions, leases and events are live state, not git. Options: a small service (Postgres or SQLite + HTTP/MCP), or GitHub-native (issues/checks) for small teams. Decide before Milestone 6.

## 10. Record types and schemas [designed]

All records: `id`, `schema_version`, `status`, `owner` (module/team), provenance (`proposed_by`, `approved_by`, timestamps), `visibility`.

Statuses: `proposed`, `approved` (requirements), `verified`, `stale`, `broken`, `unverified` (claims), `accepted`, `superseded`, `rejected` (decisions).

| Type | ID | Path | Created by | Approved by |
|---|---|---|---|---|
| Source | `SRC-*` | `kb/sources/SRC-*/` | kb ingest | — (data) |
| Requirement | `REQ-F-*`, `REQ-NF-*` | `kb/requirements/` | ingestor drafts | `approvers.intent` |
| Feature | `FEAT-*` (claims `FEAT-014.C2`) | `kb/features/` | generator drafts | module owner |
| Module | `MOD-*` | `kb/modules/` | scanner + generator | module owner |
| Decision | `ADR-*` | `kb/decisions/` | agents/people/mine-decisions | `approvers.decisions` |
| Invariant | `INV-*` | `kb/invariants.md` or files | people | tech leads; must have passing test |
| Component | `COMP-*` | `kb/components/` | generator | owner |
| Glossary term | `TERM-*` | `kb/glossary/` | ingestor/people | owner (→ ENT-* planned §21) |
| Rejected idea | `status: rejected` | alongside type | — | — |
| Change | generated | change requests / PRs | engine | — |

### source.yaml
```yaml
id: SRC-pricing-spec-v3
original: docs/requirements/04-pricing-spec-v3.pdf
original_sha256: 7f3c...
normaliser: pdf@1.4
trust: spec            # spec | design | discussion | external
visibility: internal
guard: { emails: 1, secrets: 1, injections: 0, issue: "#57" }
```
### segments.jsonl (one line each)
```json
{"id":"seg-08","page":5,"heading":"3. Seat billing > 3.2 Removing seats","start":4955,"end":5031,"hash":"41c7"}
```
### Requirement
```yaml
id: REQ-F-034
schema_version: 1
statement: No refund is given when a seat is removed.
evidence:
  mode: all            # all = every quote needed; any = independent corroboration
  quotes:
    - source: SRC-pricing-spec-v3#seg-08
      chars: [5012, 5031]
      text: "No refund is given."
features: [FEAT-014]
status: proposed
proposed_by: agent:ingestor (tier standard, prompt v7)
approved_by: null
```
### Feature claim
```yaml
- id: FEAT-014.C2
  claim: Removing a seat never issues a refund.
  requirement: REQ-F-034
  code: billing/seats.py#remove_seat
  fingerprint: a41f
  test: tests/test_seats.py::test_no_refund_on_removal
  level: E5            # required: E5 (risk high)
  judged_by: null      # "model" when E4
  confirmed_at: 3d81e0a
  status: verified
```
### Decision (ADR)
```markdown
---
id: ADR-0041
status: proposed        # proposed | accepted | superseded | rejected
supersedes: ADR-0009
decided_by: agent:claude-code@session-7f2c
approved_by: null
governs: [MOD-sessions, MOD-auth]
revisit_when: "write volume > 50k/s or cost > $X/month"
---
## Context
Sessions table: 40k writes/s at peak, p95 420 ms.
## Options
1. Postgres partitioning — ...
2. Redis — ...
3. DynamoDB with TTL — chosen
## Decision and rationale
...
## Conditions
INV-005 (30-min expiry) must hold; migrate via dual-write first.
```
### Baseline
```yaml
version: v1
frozen_at: 2026-11-16
tag: kb-baseline-v1
requirements: [{id: REQ-F-031, hash: "..."}, {id: REQ-F-034, hash: "..."}]
```
### Audit log entry (`kb/audit/YYYY-MM.jsonl`, append-only)
```json
{"at":"2026-11-20T10:41:57Z","record":"ADR-0041","from":"proposed","to":"accepted","actor":"github:lee","via":"PR#645","kb_rev":415}
```

## 11. Segments, claims and quotes [designed]

- **Segments are structural evidence** (deterministic, fixed rules). **Claims are semantic** (one fact, plain words).
- **Many-to-many:** one segment can support several claims; one claim can need several segments, from one or several sources. Evidence `mode: all` (every quote required) or `any` (independent corroboration).
- **Quote rules:** verbatim match against stored masked `text.md`; record char range; cap quotes per claim; cross-source quotes require reviewer approval.
- **Quote support check [planned]:** research (Sept 2026 preprint, unverified) found models quote 98% of claims but only ~37% of quotes fully support the claim. Add a second check: does the quote actually support the claim (model judge, labelled; sampled by approval sampler).
- **Why segments are required:** fine-grained change detection (segment hash change → only claims resting on it go stale); citations survive edits (identity by heading path + hash, not offsets alone); small model calls; per-segment masking/visibility; coverage accounting (every segment yields claims or "no requirement here").
- `kb trace <id>`: claim → quote → segment → file → page (re-checks quote); `kb trace SRC…#seg-08` → all claims resting on it.

## 12. Evidence levels, fingerprints and staleness [designed]

| Level | Meaning |
|---|---|
| E0 | No evidence |
| E1 | Anchor exists and fingerprint matches |
| E2 | Linked test passes on this commit |
| E3 | Coverage shows the test runs the anchored code (not mocked) |
| E4 | Model judges the test checks the claim (`judged_by: model`) |
| E5 | Mutation-proven: deliberately breaking the code makes the test fail |

Required level by risk tier: **low → E2, standard → E4, high → E5**.

**Fingerprint:** hash of the symbol's syntax tree with formatting and comments removed (formatting-only changes don't make claims stale).

**Staleness decision order:** anchor resolves? → fingerprint changed? → linked tests pass? → evidence still at required level? → model re-confirm (still true / changed / unsure).

Rules: flaky tests are quarantined (claims drop to E1 until fixed); a test edited together with its code is flagged `co-changed` for reviewer attention; deleting a test breaks its claims (never silently lowers level); snapshot tests count E2 at most; mocks of the anchored symbol prevent E3; mutation runs on PR only for changed high-risk symbols, full run nightly/weekly.

**When checks run:** on every PR, for everything the PR touches (code fingerprints and changed source segments). Nightly `kb sync --full` is a **backstop** for what no PR changes: sources outside the repo, expired attestations, flaky tests, `revisit_when` dates, model updates.

## 13. Test coverage strategy [designed]

- **Claim coverage, not line coverage.** `kb coverage [--module|--entity]` lists claims below their required level, riskiest first.
- **Claim-tagged tests:** `@claim("REQ-F-041")` makes the test↔claim link explicit and readable by code (replaces most E4 judging).
- **`kb propose test <claim>`:** agent drafts a tagged test from the claim + quote. Accepted only if it **passes on today's code** and **fails under mutation**. If it fails on today's code → contradiction issue (never bend the test). Owner reviews in a PR.
- **Characterization tests** for legacy code: pin current behaviour, labelled "current behaviour, not confirmed intent", E2 max.
- **Non-unit evidence** for claims tests can't prove (p95 < 300 ms): load-test result, monitoring alert on the target, or manual attestation with named owner and expiry; record names its evidence kind.
- **Coverage ratchet** in `kb verify`: new/changed claims must reach required level; module claim coverage may rise, never drop (warn-only during rollout).

## 14. Storage layout [designed]

```
kb.config.yaml                    project settings (people, by PR)
kb/
  README.md                       rules every agent follows
  sources/SRC-*/                  source.yaml · text.md (masked) · segments.jsonl
  requirements/REQ-*.yaml
  features/FEAT-*.md
  modules/MOD-*.md
  components/COMP-*.md
  decisions/ADR-*.md
  invariants.md
  glossary/                       TERM-* (→ entities/ENT-* planned)
  baseline/v1.yaml                + git tag kb-baseline-v1
  audit/YYYY-MM.jsonl             append-only status changes
  _generated/                     index, graph, cards, reports, vector index, snapshots (CI only)
CODEOWNERS                        generated from module owners
.kb/cache/                        CI cache, safe to delete
.github/workflows/kb.yml          kb verify on PRs
CLAUDE.md · AGENTS.md             agent rules + pointer to kb/README.md
.claude/settings.json             hooks
```
Outside git: CI artifacts (test history, model call log); review queue = GitHub PRs + `kb-review` issues; Phase 4 live state (sessions, leases, events) in the coordination service (§9.5).

## 15. `kb.config.yaml` [designed]

```yaml
version: 1
teams:
  team-billing:   { git_team: "@acme/billing" }
  team-platform:  { git_team: "@acme/platform" }
  product-owners: { git_team: "@acme/product" }
  tech-leads:     { git_team: "@acme/tech-leads" }
modules:
  - id: MOD-billing
    paths: ["services/billing/**"]
    owner: team-billing          # → CODEOWNERS + required approval
    risk: high                   # low | standard | high → evidence E2 | E4 | E5
  - id: MOD-sessions
    paths: ["services/sessions/**"]
    owner: team-platform
    risk: high
sources:
  - paths: ["docs/requirements/**"]
    trust: spec
    visibility: internal
  - paths: ["docs/tickets/*.json"]
    adapter: json
    mapping: { message: body, title: subject, date: created_at }
    trust: discussion
visibility:
  restricted:
    - paths: ["docs/contracts/**"]
      readers: [tech-leads]
models:                           # tier per job
  extract: standard
  judge_test: standard
  reconfirm: standard
  tie_break: small
evidence:
  required: { low: E2, standard: E4, high: E5 }
policy:
  mode: warn-only                 # warn-only during rollout → enforce
  fail_on: [broken, schema, decision_guard, missing_owner_approval, stale_context]
  warn_on: [stale, coverage_drop]
  approvers:
    intent: [product-owners]      # requirements
    decisions: [tech-leads]       # ADRs, ground-truth change requests
review:
  max_items_per_pr: 40
  escalate_after_days: 5
coordination:                     # Phase 4
  lease_minutes: 30
  broadcast_rate_per_hour: 6
  ask_reply_timeout_minutes: 10
```
Ownership: `owner` → generated CODEOWNERS + branch protection; CI rule `missing-owner-approval` re-checks it. Agents run as bot accounts; their approvals never count. Unknown teams or files in no module/two modules → config error.

## 16. Commands [designed unless tagged]

| Command | When | Where | Writes / effect | Blocks? |
|---|---|---|---|---|
| `kb init` | once, adopting | repo root, new branch | draft config (owners TODO, warn-only), `kb/` skeleton, CI workflow, CLAUDE.md/AGENTS.md pointer, hooks; dry check of module coverage. No model, no facts. | no |
| `kb ingest` | after init; new docs | CI or laptop | `kb/sources/*`, proposed REQ-*, ingest report, PR | no (review) |
| `kb generate` | after ingest | CI or laptop | structure claims, cards, behaviour drafts, FEAT/MOD pages, alignment issues, PR | no |
| `kb mine-decisions` | once on history | CI or laptop | ADR drafts marked `reconstructed`, every reason cited | no |
| `kb trace <id>` | any time | anywhere | nothing (shows chain, re-checks quote) | no |
| `kb baseline freeze v1` | after Phase 1 approval | CI/laptop | `kb/baseline/v1.yaml` + tag | no |
| `kb check --changed` | every commit / agent Stop hook | laptop | nothing (warnings), regenerates cards | warn |
| `kb verify` | every PR | CI | statuses, evidence, audit log, PR comment; coverage ratchet; stale-context check | **yes** |
| `kb context --task "…"` | agent session start | MCP/CLI | nothing (context pack) | no |
| `kb impact <path>` | before edits; in verify | anywhere | nothing | no |
| `kb propose` / `propose decision` / `propose test <claim>` | end of agent task / choosing options / closing gaps | agent branch | drafts into the PR | via verify |
| `kb coverage` | any time; in verify | anywhere | report | ratchet in CI |
| `kb sync --full` | nightly backstop | scheduled CI | stale marks, issues, full mutation, sampling, coverage report | warn (issue) |
| `kb snapshot vX` | release | CI on tag | `kb/_generated/snapshots/vX/` + tag | no |
| `kb migrate` | framework upgrade | CI as PR | migrated records | schema errors fail |
| `kb request-change <record>` | change accepted ground truth | MCP/CLI | proposed superseding record; notices | nothing changes until approved |
| `kb ask "<q>"` | before acting on shared things | MCP | answer; routes intent to owners | no |
| `kb session` | hooks: start/edit/stop | MCP | registry, leases, notices, revalidation | warns on overlap |

Adoption order: `kb init` → fix owners, merge → `kb generate` → `kb ingest` → `kb mine-decisions` → review sprint (3–5 modules) → `kb baseline freeze` → 2–4 weeks warn-only → enforce (fail_on, require code-owner review + KB status check, set risk: high where needed).

## 17. Agent integration [designed; hook format open]

### CLAUDE.md / AGENTS.md rules (same content)
1. Before a task: `kb context --task "<task>"` (or `kb_context`); before editing a file: `kb_for_path`.
2. Cite KB IDs for every statement about the product.
3. Treat `stale`, `broken`, `unverified` records as unknown until checked in code.
4. Never act against an accepted decision or invariant. Choosing between options or changing one → `kb propose decision` / `kb_request_change`, then stop for a human if it changes ground truth.
5. If you change behaviour a claim describes, add/update its `@claim`-tagged test.
6. Before changing shared things (navigation, branding, APIs, schemas) others may be working on → `kb_ask`.
7. On a change notice: re-plan (continue/adapt/wait/stop) before continuing.
8. Never edit approved records or `kb/_generated/` directly.
9. Finish with `kb_propose` and `kb_check`; the task isn't done until checks pass.

### Hooks (illustrative — verify against current Claude Code hook docs before shipping) [open]
- SessionStart → `kb session start && kb context --session-brief`
- PostToolUse (Edit|Write) → `kb impact --from-hook && kb session lease --from-hook`
- Stop → `kb check --changed --from-hook`

### MCP tools
| Tool | Returns |
|---|---|
| `kb_context(task)` | context pack (cards, claims, governing records, stale/broken warnings) |
| `kb_for_path(path)` | records that anchor to or govern a file |
| `kb_get(id)` | one record with evidence and status (access-checked) |
| `kb_search(query)` | matching records, verified first (access-checked, IDs only) |
| `kb_impact(paths)` | affected claims, requirements, decisions, owners, tests |
| `kb_propose(changes)` | draft KB updates in the current branch |
| `kb_check()` | local checks |
| `kb_coverage(module)` | claims below required level |
| `kb_request_change(record, proposal)` | Phase 4 change request |
| `kb_ask(message)` | Phase 4 question/answer |
| `kb_session(...)` | register, heartbeat, lease, notices |
| `kb_entity(id)` [planned] | everything about an entity |

Other agents (Cursor, Codex…): read AGENTS.md, use the same MCP tools. If any agent skips the rules, CI enforces them anyway.

## 18. CI gate and enforcement rules [designed]

| Rule | Enforced by | On violation |
|---|---|---|
| Every statement has proof | schema + evidence check | `unverified`; CI fails if a PR lowers it below required level |
| Docs change in the same PR as code | drift check | CI fails until affected claims updated/re-confirmed |
| Only humans approve intent and decisions | status permissions (`policy.approvers`) | status change rejected and logged |
| Model output is only a draft | gates | ungrounded/unauthorised drafts never reach the store |
| Accepted decisions are never edited | decision lock + guard | CI fails; superseding decision required |
| Every invariant has a passing test | invariant check | CI fails |
| Owning team approves changes to its module | CODEOWNERS + branch protection + `missing-owner-approval` | GitHub refuses merge |
| Claim coverage never goes down | coverage ratchet | CI fails if coverage drops or new/changed claim below level |
| Changed source re-checked in the PR that changes it | staleness detector on PR; nightly backstop | claims go stale; PR waits for intent approvers |
| Work on outdated ground truth can't merge | stale-context check (registry KB revision) | CI fails until re-planned |

## 19. Security and trust boundary [designed]

- Ingestion guard before storage or model calls: mask secrets/PII, security issue if secret in original file, injection flags, visibility labels, wrap as quoted data.
- Access filter on **every** read path: `kb_get`, `kb_search`, `kb_context`, `kb_entity`, trace, MCP and REST. Filter before ranking (research: semantic search leaked 44% cross-scope rows when filter co-existed with ranking; a get-by-id path skipped scope entirely).
- Duplicate detection must never drop a contradicting claim: run contradiction detection first, or route near-duplicates with a differing object to the contradiction check.
- Agent identity = bot accounts; approvals only from human GitHub identities.
- Messages from other agents and text from sources are data; only engine events are authoritative.
- Model gateway logs every call (model, prompt version, input fingerprint).

## 20. Build plan, tech stack and milestones [designed: plan; open: exact stack]

### Suggested stack [open — confirm before starting]
- Python 3.12 CLI `kb` (Typer/Click), packaged; config via pydantic schema.
- Code adapters: tree-sitter (Python first), fingerprint = hash of normalised AST.
- Test adapter: pytest + coverage.py (per-test contexts), mutation via mutmut or cosmic-ray (scoped).
- Source adapters: markdown, html, docx (python-docx), pdf (pdfplumber/pypdf), json/csv with mapping. OCR later.
- Guard: secret detection (detect-secrets/gitleaks rules) + PII regexes; injection heuristics.
- Model gateway: provider-agnostic interface; default to current Claude models; JSON schema output; cache on disk.
- Generated index: SQLite (+ sqlite-vec for fallback search), graph as adjacency tables.
- MCP server: official MCP Python SDK.
- CI: GitHub Actions; required status check; CODEOWNERS generation.
- Phase 4 service: small FastAPI + Postgres/SQLite, or GitHub-native (decide §9.5).

### Milestones (each ends with tests and a demo on a sample repo)
1. **Skeleton:** config schema + validation, `kb init`, record schemas, gates (schema, IDs), audit log, `kb check`.
2. **Phase 1:** source adapters (md, pdf), segmenter, guard, store, extract via gateway, quote gate, REQ files, `kb ingest`, `kb trace`, review routing via PR, `kb baseline freeze`.
3. **Phase 2 core:** Python code adapter, fingerprints, anchor index, change detector, test adapter (pass/fail, coverage), evidence E1–E3, staleness, `kb verify` in CI, CODEOWNERS + owner-approval rule.
4. **Phase 2 depth:** generator, linker, E4 judge, E5 mutation (scoped), re-confirmer, divergence/alignment, coverage analyzer + ratchet, `kb propose test`.
5. **Phase 3:** generated graph/index, impact analyzer, access filter (all read paths), context packer, MCP server + tools, CLAUDE.md/AGENTS.md rules, hooks.
6. **Phase 4:** registry + leases, change requests, event bus, revalidator, `kb ask`, stale-context CI check.
7. **Operations:** `kb sync --full`, snapshots, approval sampler, schema migrator, decision mining.
8. **Entity extension** (§21).

### Evaluation (build alongside, like an ArgusFleet-style harness)
- Golden question set (≈50) answered by an agent with/without the KB: wrong-fact rate, citation rate.
- Gate tests: invented quotes rejected; contradictions never dropped.
- Leakage probes on every read path (expected 0).
- Staleness: affected-claim precision/recall on seeded changes; false-alarm rate.
- Review load: time per claim, items per PR.
- Phase 4: notice reaches only affected sessions; stale-context PRs blocked.

## 21. Entity-driven extension [planned]

Make business concepts (Seat, Subscription, Invoice, Plan, Webhook) the spine. Our entities are **types** (what a Seat is/does); enterprise systems hold **instances**.

```yaml
# kb/entities/ENT-seat.yaml
id: ENT-seat
name: Seat
aliases: [seat, licence, user slot]
definition: A paid place for one user on a subscription.
  quotes: [SRC-pricing-spec-v3#seg-07]
owner: MOD-billing
relations: [{belongs_to: ENT-subscription}, {billed_on: ENT-invoice}]
states: [active, pending_removal, removed]
anchors: [services/billing/models.py#Seat, "db: table seats", "api: openapi#/components/schemas/Seat"]
authority: [SRC-pricing-spec-v3, SRC-functional-reqs, SRC-support-tickets]   # rank order
visibility: internal
```
Claims gain `about: [{entity, attribute}]`, `valid_from`, `valid_to`.

Where it plugs in: `kb init` proposes entities from ORM models/migrations/OpenAPI; new Phase 1 step **Link entities** (after Store, before Extract) tags segments by alias; gate: every claim names an entity+attribute; **conflict check by (entity, attribute)** using `authority` then recency, else contradiction issue; entity pages; `kb coverage --entity`; context resolves tasks to entities first; access per entity; validity dates enable `kb context --as-of`.

Rollout: 1 registry from code+glossary → 2 tag claims with `about` → 3 conflict check + authority → 4 entity pages, `kb_entity`, entity-first context → 5 validity dates + webhook sync for outside sources.

## 22. Lessons from research and the market (Nov 2026)

**Market:** nothing implements the full design. Closest for software KB verification: **Tracey** (open source: requirement↔code↔test markers, staleness by version, MCP tools, CI validation; no test-pass checks, evidence levels, quotes or approvals). **Sphinx-Needs + sphinx-test-reports** (traceability + test evidence + CI gates). **GitHub Copilot Memory** (citations re-checked for existence only). **Swimm** (code-coupled doc sync). For enterprise entity KBs: **Palantir Foundry Ontology + AIP** closest commercially; **Zep/Graphiti** (bi-temporal graph, provenance, MCP, no auth in reference servers); **Caura/MemClaw** (scopes, trust tiers, tenancy, audit). Gaps nobody fills: quote-verified claims, evidence levels combining pass/coverage/mutation, knowledge-specific approval flows, authority-based truth engine, bi-temporal facts in enterprise platforms.

**From "Governed Shared Memory for Multi-Agent LLM Systems" (Margalit et al., arXiv 2606.24535, MemClaw/ArgusFleet):**
- Four failure modes to test for: unauthorized leakage, stale propagation, contradiction persistence, provenance collapse.
- Get-by-id path skipped sub-tenant scope (fixed) → enforce access on every path.
- Semantic search leaked 44% of cross-fleet rows → filter before ranking.
- Sync dedup gate rejected contradicting writes before the async contradiction detector (only 49% reached it) → contradiction check first.
- Fleet-internal sharing shouldn't need admin trust.
- Provenance chains reconstructed 100%; consistency cost paid at write time.

**Enterprise profile (later):** the PDF design (entity resolution across CRM/billing/events, CDC → event bus → truth engine with `source_system`, `timestamp`, `authoritative_rank`, polyglot stores, scoped context for spawned agents, reactive triggers). If ever built: buy/integrate MDM (Reltio/Senzing), streaming (Confluent), temporal graph (Graphiti); build truth engine, scoped context API, approval queue. Shared with our core: fact model, trust rules, context API — not runtime.

## 23. Open questions

1. Exact tech stack (§20) — confirm.
2. Where Phase 4 live state lives (§9.5).
3. Claude Code hook names/format — verify against current docs.
4. Quote-support model check thresholds and sampling rate (§11).
5. Default token budget for context packs and layer sizes.
6. Which languages beyond Python for code adapters, and priority.
7. Whether `kb ask` answers may auto-apply when the KB already settles the question (no human) — proposed: yes for "no conflict" answers that change nothing.

## 24. Appendix: worked examples

**A. Pricing spec → requirement:** `04-pricing-spec-v3.pdf` page 5 "3.2 Removing seats" → seg-08 (chars 4955–5031, hash 41c7) → drafts "removed seat stays active until end of cycle" (REQ-F-033) and "No refund is given…" (REQ-F-034, quote at 5012–5031) → draft "Annual plans get two months free" rejected (quote not found) → PR #41 approved by @acme/product, REQ-NF-004 edited to "p95 < 300 ms" → baseline v1.

**B. Contradiction:** `kb generate` finds `seats.py` refunds pro-rata vs REQ-F-034 → issue to owners → team fixes code, adds `test_no_refund_on_removal` → FEAT-014.C2 at E5.

**C. Code change PR:** PR #612 refactors `delivery.py#deliver` → fingerprint 7c2e≠a41f → FEAT-021.C2/C3 stale → tests pass at E4 → model re-confirms → gates → verified → team-platform approves → merge.

**D. Spec change:** v4 of pricing spec in PR #640 → seg-08 hash 41c7→9d02 → REQ-F-033/034 stale → PR waits for product approval of new wording.

**E. Multi-agent (Phase 4):** agents A (Claude Code, Dana's laptop), B (Cursor, Sam's laptop), C (Claude Code, cloud), E (Codex, cloud) register at KB rev 412 → A requests ADR-0041 (DynamoDB) superseding ADR-0009 → B, C get pending notice; E nothing → lee approves with conditions (INV-005 holds, dual-write first) → rev 415, tasks T-1..T-3 → notices to A, B, C → A continues T-1, B continues, C waits for T-2 → C's PR #640 (rev 412) fails stale-context until re-planned → B's `kb ask` about moving Billing alerts asks only E (lease on settings), finds REQ-F-052, product owner approves with condition, REQ-F-052 v2 + ADR-0043, both notified.
