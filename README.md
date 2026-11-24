# grounded-kb

`kb` is a git-native knowledge base for a software repository. Product documents (PDF, Word, Markdown) are stored as
sources; your own coding agent drafts requirements, decisions and invariants from them, each resting on a verbatim
quote; deterministic gates (`kb check`) keep every record grounded and every marker in the code pointing at a real
record. Nothing calls a model: the tool reads, masks, segments, checks and reports.

Platform: POSIX only (macOS, Linux). It relies on `O_NOFOLLOW` / `dir_fd` file access and POSIX shell hooks; Windows
is not supported.

## Install

Python 3.12 or later and [uv](https://docs.astral.sh/uv/). Either from PyPI or from a pinned git tag:

```sh
uv tool install grounded-kb                                                       # from PyPI
uv tool install 'git+https://github.com/mrashidramzan1996/grounded-kb@v0.1.0'     # a pinned tag
kb version
```

For CI, `kb init --install-spec 'grounded-kb==0.1.0'` (or the pinned git spec above) writes it into the workflow;
without `--install-spec` the CI job only warns.

The generated CI job runs `kb verify --base origin/<target> --comment` on pull requests (one updated comment, plus
`.kb/cache/status.json` and `.kb/cache/verify.md` as an artifact) and `kb verify --all` on the default branch. Install your
project's test dependencies in the workflow before the verify step, keep `fetch-depth: 0` so the merge-base exists, and
grant `pull-requests: write` for the comment (the template does).

## Use (Phase 1)

| Command | What it does |
| --- | --- |
| `kb init` | Scaffold `kb.config.yaml`, the agent rules in `AGENTS.md` / `CLAUDE.md`, a CODEOWNERS block, a `.gitignore` block for `.kb/` and CI. Modules come only from CODEOWNERS directory rules; owners and approvers stay TODO. |
| `kb source add <doc>…` | Read, mask (secrets, emails), segment and store documents under `kb/sources/`. |
| `kb source sync` | Re-read every stored source from its original (segment ids stay stable). |
| `kb ingest [paths]` | Store the sources and write a drafting brief to `.kb/cache/ingest/brief.md` for your coding agent. Files no reader takes are skipped. |
| `kb ingest --report [--strict]` | Gate what the agent drafted since `kb ingest` ran: accepted, rejected, flagged, sources, untriaged segments, proposed modules. |
| `kb module add MOD-x --owner <@handle\|TODO>` | Add a module without paths. |
| `kb module paths MOD-x --accept` | Write the paths suggested from `r[impl ID]` markers (or `--set GLOB`). |
| `kb check [--strict] [--changed] [--base REF]` | Run every gate: config, records, quotes, anchors, sources (re-derived from the originals), modules, triage. |
| `kb context --paths F / --module M / --id X` | The live requirements, decisions and invariants governing what you are about to touch. |
| `kb trace <ID>` | The evidence chain of a record, its quotes re-checked. |
| `kb status` | Per module: how many live requirements are built (`r[impl]`) and tested (`r[verify]`). |
| `kb baseline freeze v1` | Record the agreed requirement set on the default branch's tip (refused while `kb check` reports errors). |
| `kb agents install claude-code / codex / generic` | Wire the session-start and stop hooks, or a git pre-commit hook. |
| `kb hook <event>` | The hook entry point the agents call; it blocks only on errors new since the default branch. |

Exit codes: 0 ok, 1 policy failure, 2 usage, config or tool error. `--format json` goes to stdout, messages to stderr.

The walk-through on a sample repository is in [`docs/phases/phase1-demo.md`](docs/phases/phase1-demo.md); the
design is in [`kb_spec.md`](kb_spec.md).
