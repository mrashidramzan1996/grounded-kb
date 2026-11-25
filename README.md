# grounded-kb

`kb` is a git-native knowledge base for a software repository. Product documents (PDF, Word, Markdown) are stored as
sources; your own coding agent drafts requirements, decisions and invariants from them, each resting on a verbatim
quote; deterministic gates (`kb check`) keep every record grounded and every marker in the code pointing at a real
record. Nothing calls a model: the tool reads, masks, segments, checks and reports.

Platform: POSIX only (macOS, Linux). It relies on `O_NOFOLLOW` / `dir_fd` file access and POSIX shell hooks; Windows
is not supported.

## Agent-driven mode

You can leave the kb commands to your coding agent. Run `kb init` and `kb agents install claude-code` (or `codex`,
`generic`) once, commit, and tell the agent:

> Set up the knowledge base from the documents in docs/.

The playbook in `AGENTS.md` (and the `kb` skill for Claude Code) tells the agent to run `kb todo`, do every `do` item
itself (ingest, draft, triage, sync, redraft, fix, and commit once `kb ingest --report` or `kb redraft --report`
passes), and ask you every `ask` item in one message, with evidence and lettered options. Expect questions like these: who approves requirements and decisions, who owns each module, what
measurable target a vague line such as "The checkout page must be fast." should have, whether to remove a secret
from a document, and whether to freeze the baseline. Short answers are enough ("1a @acme/product, 2a under 2
seconds", and your handle so the agent can record who decided). The agent writes handles into `kb.config.yaml` and
commits that file on its own, records each other fact with `kb clarify` so a redrafted requirement can quote your
answer, and redrafts every answered requirement in one `kb redraft`. Neither kb nor the agent answers a question for
you. On "freeze" the agent commits `kb/baseline/v1.yaml` on a new branch and opens a pull request; approval is still
a merged pull request, and the agent never merges it. A repository set up before 0.3.0 gets the playbook from
`kb agents install <name>`. Later, "I added docs/x.md" or "I
updated the pricing spec" is enough: `kb todo` lists the ingest, sync and redraft work. The walk-through is in
[`docs/phases/agent-mode-demo.md`](docs/phases/agent-mode-demo.md).

## Install

Python 3.12 or later and [uv](https://docs.astral.sh/uv/). Either from PyPI or from a pinned git tag:

```sh
uv tool install grounded-kb                                                       # from PyPI
uv tool install 'git+https://github.com/mrashidramzan1996/grounded-kb@v0.3.0'     # a pinned tag
kb version
```

For CI, `kb init --install-spec 'grounded-kb==0.3.0'` (or the pinned git spec above) writes it into the workflow;
without `--install-spec` the CI job only warns.

The generated CI job runs `kb verify --base origin/<target> --comment` on pull requests (one updated comment, plus
`.kb/cache/status.json` and `.kb/cache/verify.md` as an artifact) and `kb verify --all` on the default branch. Install your
project's test dependencies in the workflow before the verify step, keep `fetch-depth: 0` so the merge-base exists, and
grant `pull-requests: write` for the comment (the template does).

## Approvals

On a pull request, `kb verify` reports `missing_owner_approval` until the right people approve the head commit:
CODEOWNERS (or, without one, the module owners) for the files changed, `policy.approvers.intent` for changed or stale
requirements, features and glossary terms, and `policy.approvers.decisions` for changed decisions, invariants and
components. The approval policy
(`policy.approvers`, `teams`, `bots`, module owners, `fail_on` and `mode`) and CODEOWNERS are read from the current tip
of the target branch (`--base`, else the default branch), not from the pull request and not from the merge-base, so a
change cannot loosen the rules it is checked against, not even by branching from an old commit; the Approvals section
of the report says where they came from (`Policy from base main at <sha>.`). A change to `kb.config.yaml` or to a
CODEOWNERS file (`CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS`) also needs the decision approvers. With
`kb verify --all` and a pull request, the policy comes from the branch the pull request targets (or the default
branch); a base branch that cannot be found is an error (exit 2). Without a pull request and without a base the working
tree's policy applies. Only when the target branch has no `kb.config.yaml` yet does the change's own policy apply, with
the target branch's CODEOWNERS.

To bootstrap, name the approvers on the default branch first: commit `policy.approvers.decisions` (and `intent`) there
before any pull request changes the config. While the default branch names no decision approvers, a pull request that
changes `kb.config.yaml` or CODEOWNERS cannot pass the approval check.

Team approvers (`@org/team`) are checked against the forge's team membership. When the forge cannot read a team
(a token without organisation read access), the approval does not count and `missing_owner_approval` is reported,
unless the team's members are listed in the base branch's config:

```yaml
teams:
  product:
    git_team: "@acme/product"
    members: [alice, bob]   # forge logins, no @; used only when the forge cannot read the team
```

Set `policy.unreadable_teams: defer` to restore the old behaviour (any approval is accepted with an
`owner_approval.deferred` warning, leaving the check to branch protection's required code-owner review).

## Use (Phase 1)

| Command | What it does |
| --- | --- |
| `kb init` | Scaffold `kb.config.yaml`, the agent rules in `AGENTS.md` / `CLAUDE.md`, a CODEOWNERS block, a `.gitignore` block for `.kb/` and CI. Modules come only from CODEOWNERS directory rules; owners and approvers stay TODO. |
| `kb source add <doc>…` | Read, mask (secrets, emails), segment and store documents under `kb/sources/`. |
| `kb source sync` | Re-read every stored source from its original (segment ids stay stable). |
| `kb ingest [paths]` | Store the sources and write a drafting brief to `.kb/cache/ingest/brief.md` for your coding agent. Files no reader takes are skipped. |
| `kb ingest --report [--strict]` | Gate what the agent drafted since `kb ingest` ran: accepted, rejected, flagged, sources, untriaged segments, proposed modules. |
| `kb redraft [IDs] [--flagged] [--changed [--base REF]] [--new]` | Write a brief to `.kb/cache/redraft/brief.md` for redrafting only the named records, the flagged requirements, or the records citing text a `kb source sync` changed (changed text no record cites is offered to draft from); run it before committing the sync, or pass `--base <commit before the sync>` with `--changed` after. `--new` adds the segments with no triage entry that no record cites, to draft new records from. |
| `kb redraft --report [--strict] [--base REF]` | Gate the redraft against the brief's commit (or `--base REF`): redrafted, drafted (new records quoting only the new or changed segments), rejected (out of scope, changed ids, `vague` removed without a target the statement quotes from its own or the offered text, edited ADRs/INVs), flagged (including new segments left untriaged, and untriaged segments a record cites, to triage by hand), sources, still flagged, not redrafted. |
| `kb todo [--format json]` | What your coding agent does next (`do` items, most with the command to run) and what it must ask you (`ask` items, with evidence and options). Once a baseline exists, work on the default branch starts with a `branch` item (`git switch -c kb/update-<date>`), and "Freeze baseline vN?" is offered only on the default branch itself. It also lists `report:ingest` / `report:redraft` while a brief's result is not committed, `commit` while `kb/`, `kb.config.yaml` or `docs/clarifications.md` has uncommitted changes, and an `unreadable:` question for a document `kb ingest` cannot read. Exit 0 whatever is listed; `state.clean` is true when there are no errors, no `do` items and no blocking questions. |
| `kb clarify --topic T --text "…" --by @handle [--date YYYY-MM-DD]` | Record a person's answer as a section of `docs/clarifications.md`, stored as a source, and print the segment to quote (`SRC-clarifications#seg-NN`). Refuses emails, secrets, markup, answers over 1500 characters, topics over 200 or containing ` > `, and handles over 64 characters. |
| `kb module add MOD-x --owner <@handle\|TODO>` | Add a module without paths. |
| `kb module paths MOD-x --accept` | Write the paths suggested from `r[impl ID]` markers (or `--set GLOB`). |
| `kb check [--strict] [--changed] [--base REF]` | Run every gate: config, records, quotes, anchors, sources (re-derived from the originals), modules, triage. |
| `kb context --paths F / --module M / --id X` | The live requirements, decisions and invariants governing what you are about to touch. |
| `kb trace <ID>` | The evidence chain of a record, its quotes re-checked. |
| `kb status` | Per module: how many live requirements are built (`r[impl]`) and tested (`r[verify]`). |
| `kb baseline freeze v1` | Record the agreed requirement set on the default branch's tip (refused while `kb check` reports errors). |
| `kb agents install claude-code / codex / generic` | Wire the session-start and stop hooks, or a git pre-commit hook, and update the kb block (rules and playbook) in `AGENTS.md`. |
| `kb hook <event>` | The hook entry point the agents call; it blocks only on errors new since the default branch. |

Exit codes: 0 ok, 1 policy failure, 2 usage, config or tool error. `--format json` goes to stdout, messages to stderr.

The walk-through on a sample repository is in [`docs/phases/phase1-demo.md`](docs/phases/phase1-demo.md); the
design is in [`kb_spec.md`](kb_spec.md).
