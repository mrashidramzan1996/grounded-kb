# Phase 1 live demo (to be run by the user)

Status: **to be run by the user.** Nothing below has been run against `/Users/dev/projects/acme-billing`; the same story
is proved automatically on a fixture copy by `tests/e2e/test_phase1.py`. Run it by hand, then fill in the result tables.

The sample repo is a git repository whose `docs/` holds `pricing-spec-v3.pdf`, `product-brief.docx` and
`architecture-notes.docx` (made by `examples/acme-billing/make_docs.py`). Commit or stash anything else first.

## 0. Install the wheel

```sh
cd /Users/dev/projects/kb
uv build --wheel --out-dir /tmp/kb-dist
uv tool install --force /tmp/kb-dist/grounded_kb-0.4.1-py3-none-any.whl
kb version                      # prints 0.4.1
```

## 1. Init

```sh
cd /Users/dev/projects/acme-billing
kb init
```

Expect: `wrote kb.config.yaml`, `.gitignore` (a kb block with `.kb/`), `AGENTS.md`, `CLAUDE.md`, `CODEOWNERS`,
`kb/README.md`, `.github/workflows/kb.yml`, and `next: set policy.approvers ...`. The CI job installs nothing until you
set its `KB_INSTALL_SPEC` (it only warns): `grounded-kb` is not published on PyPI, and an unpinned name could be taken by
anyone. Use a pinned spec you control, e.g. `kb init --install-spec 'git+https://github.com/<owner>/grounded-kb@<tag>'`,
or the published package at an exact version once its owner publishes it (see `kb/README.md`). Edit `kb.config.yaml`: replace the two `[TODO]` entries with forge handles, e.g.
`intent: ["@acme/product"]` and `decisions: ["@acme/tech-leads"]`. Then `git add -A && git commit -m "kb init"`.

## 2. Ingest

```sh
kb ingest docs/
```

Expect output ending in `Brief: .kb/cache/ingest/brief.md` and `Ask your coding agent: *follow .kb/cache/ingest/brief.md*`.
`kb/sources/` now holds three sources (`SRC-pricing-spec-v3`, `SRC-product-brief`, `SRC-architecture-notes`);
emails and the example AWS key are masked there. Files in `docs/` that no reader takes (images, say) are listed as
`skipped (no reader): …`. `kb ingest` also records the commit it ran on in `.kb/cache/ingest/base`; the brief names it.
Commit: `git add -A && git commit -m "sources"`.

## 3. Draft with your coding agent

In Claude Code or Codex, started in `/Users/dev/projects/acme-billing`, say: `follow .kb/cache/ingest/brief.md`.
The agent writes records under `kb/` and triage files, and adds modules with `kb module add`. Then:

```sh
kb ingest --report
```

The report compares against the commit `kb ingest` recorded, so it still sees the drafts after you commit them; if it
warns `nothing drafted since <sha>`, the agent has not written anything yet. Expect Markdown with `## Accepted (N)`, `## Rejected`, `## Flagged`, `## Sources`, `## Untriaged segments` and
`## Proposed modules`. With a careful agent, expect roughly: a few requirements and 2 decisions/invariants accepted;
any invented quote rejected as `quote.not_found`; any decision or invariant without quotes rejected as
`evidence.missing`; a proposed module with paths rejected as `ingest.module_paths`; "The checkout page must be fast." flagged `requirement.vague`;
the Contact segment untriaged; modules proposed without paths. `source.secret_in_original` is listed under Sources
(the architecture notes contain the AWS example key).

**Record the numbers here:**

| Accepted | Rejected | Flagged | Untriaged segments | Proposed modules |
|---|---|---|---|---|
| _ | _ | _ | _ | _ |

## 4. Resolve and check

- Remove or fix every rejected record; give each untriaged segment a record or `"none: <reason>"` in its `triage.yaml`.
- Replace TODO module owners and approvers with real handles before `kb check --strict` (`config.owner_todo` is an
  error; the agent only sets an owner when the documents name a forge handle such as `@acme/billing`).
- Fix the original: delete the "Staging access key" paragraph from `docs/architecture-notes.docx`, then run
  `kb source sync` and commit the document and `kb/sources/` together.

```sh
kb check --strict               # exit 0 once everything above is done; before the fix: error source.secret_in_original
```

## 5. Baseline

`kb baseline freeze` refuses while `kb check` reports any error, and off the tip of the default branch (a baseline
records what was approved by merging); `--allow-branch` overrides the second.

```sh
kb baseline freeze v1           # wrote kb/baseline/v1.yaml; tagged HEAD as kb-baseline-v1; commit the baseline file
git add -A && git commit -m "baseline v1"
kb status                       # one line per module and a total, all "0/<N> built, 0/<N> tested"
```

## 6. Context and trace

```sh
kb context --module MOD-billing # live requirements (not_built), decisions and their verbatim quotes
kb trace REQ-F-002              # pricing-spec-v3.pdf, page 1, "3.2 Removing seats", the quote re-checked
```

(Use the IDs your agent actually drafted; REQ-F-002 is the "no refund when a seat is removed" requirement if it kept the usual numbering.)

## 7. Build with the agent

Ask the agent to build seat removal following `kb context`. It should add `# r[impl REQ-F-002]` in code and
`# r[verify REQ-F-002]` in a test. Then:

```sh
kb status                       # the requirement shows built, then tested
kb check                        # warning module.paths_suggested: implemented in services/billing/ ...
kb module paths MOD-billing --accept
kb context --paths services/billing/seats.py   # now includes the requirement
```

Optional: `kb agents install claude-code`, start a session, introduce a marker with an unknown ID; the stop hook
blocks with `marker.unknown_id` until it is fixed. Hooks block only on errors that are new since the merge-base with the
default branch; TODO owners and secrets in originals are printed as notes (a new secret does block `pre-commit`).

## 8. Baseline eval (manual)

```sh
cd /Users/dev/projects/kb
uv run python eval/run.py --agent claude --with-kb    --repo /Users/dev/projects/acme-billing
uv run python eval/run.py --agent claude --without-kb --repo /Users/dev/projects/acme-billing
```

The runner is sandboxed read-only. It refuses to run (exit 2) without `kb.config.yaml` and registered sources, and while
`kb check` reports `source.secret_in_original` (finish step 4 first). `--without-kb` runs in a temporary copy without
`kb/`, `.kb/`, `.claude/`, `.codex/` and the kb blocks of `AGENTS.md` / `CLAUDE.md`; claude runs with
`--setting-sources project` in both modes (the flag is listed by `claude --help`). The copy gets a fresh one-commit git
repository, so its git history, status and diff don't reveal the removed KB. It sends the questions and the repository content the agent reads to the agent's model provider,
and writes the answers to `eval/results/phase1-claude.json` (decide before committing that file). Record the rates here:

| Agent | Mode | Wrong-fact rate | Citation rate |
|---|---|---|---|
| claude | with-kb | _ | _ |
| claude | without-kb | _ | _ |
