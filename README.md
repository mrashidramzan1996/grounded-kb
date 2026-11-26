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

## Evaluate the extraction

`kb check` proves that every record rests on a verbatim quote. It cannot tell whether the agent extracted every
requirement, or whether each record says what its quote says. The evaluation measures both, and a person decides what
it finds. kb still calls no model: your coding agent does the extraction and the judging, following briefs, and kb
checks everything they write.

1. `kb eval extract` writes a blind brief, `.kb/cache/eval/extract.md`. It holds every stored segment and nothing of
   the knowledge base. A fresh session or subagent follows it and writes a second extraction ("run B") under
   `.kb/cache/eval/run-b/`, with `B-` ids. kb sets aside any run B file whose quotes it cannot verify
   (`eval.run_b_invalid`) and never copies anything from run B into `kb/`.
2. `kb eval judge` aligns run B with the knowledge base by quote spans. It writes one item per question to
   `.kb/cache/eval/judge.md`: each live requirement and exclusion (does it say what its text says?), each rule-like
   sentence no record quotes, each run B record that matches no kb record, splits and merges, and the glossary's terms
   and relations along with the entities and relations run B or kb's detector found that the glossary lacks. Items
   are named by opaque ids (`F-` and ten hex digits, new for each judge run) and never say which extraction they came
   from. Another fresh session or subagent judges each item on its own and writes `.kb/cache/eval/verdicts.yaml`. It
   should flag an item whenever it is unsure.
3. `kb eval report` checks every verdict: the span must appear word for word in the item's text, every value must be
   one the item allows, and every id named must be live. It prints coverage, the agreement with run B (span,
   entity and relation F1) and the ranked findings: omissions, unfaithful records, records to split or merge.
   Each finding lists its supporters among kb's coverage gate (or entity detector), run B and the judge. A missing or
   invalid verdict exits 1. `--write` also writes `kb/eval/summary.yaml`, which you commit.
4. Each open finding becomes a blocking `ask` item in `kb todo` (`finding:F-…`), with evidence and options, and an
   open finding blocks the freeze: `kb baseline freeze` refuses (exit 1) while the evaluation is missing or stale, a
   finding is open or the judge's dismissals are not agreed with, unless `--unevaluated` (which records `eval: null`).
   A baseline records the evaluation it was frozen with (`eval:` commit, fingerprint, open, decided and dismissed
   counts). The person answers, and the agent records each answer with
   `kb eval decide F-… --as <decision> --by @handle`: `added --record <new id>` (a new record, or a TERM, that quotes
   the text; run `kb ingest --report` first), `covered --record <id>`, `not_a_rule --reason "…"`,
   `product_decision --reason "…"`, `correct`, `fixed --record <id>` (redrafted since the evaluation) or `ok`. kb
   checks that the knowledge base shows the decision and refuses one it cannot verify. Decisions go to
   `kb/eval/decisions.yaml`. A sentence decided `added`, `covered` or `not_a_rule` stops warning `sentence.uncovered`
   and is not asked again in later evaluations. A decision on a record stops applying once the record changes. Once
   there is an evaluation and no live REQ-NF has the category security, privacy or availability, `kb todo` also asks
   whether none apply (`categories`, optional, though the freeze is offered only once it is answered;
   `kb eval decide categories --as not_a_rule --reason "…" --by @handle`). A finding that needs a product decision:
   record the user's answer with `kb clarify`, draft a record quoting both the document sentence and the
   clarification, run `kb ingest --report`, then decide `--as added`; the new clarification that record quotes keeps
   the summary current.
5. The judge alone never settles an item kb or run B flagged. A `covered` verdict must name a record whose found
   quotes overlap the text (for an entity, a TERM carrying its name; for a relation, a TERM holding a relation to
   the other entity's TERM), or it is rejected. Every item judged `covered`, `not_a_rule` or `not_an_entity` is listed
   under `dismissed:` in `kb/eval/summary.yaml`, and `kb todo` asks about them all in one blocking question
   (`dismissed`, at most 50 shown; `kb eval status` lists every one). The person agrees with
   `kb eval decide dismissed --as ok --by @handle`, which is pinned to that exact list (a new judge run asks again), or
   reopens each item they disagree with by `kb eval decide F-… --as reopen --by @handle`: it becomes an open omission
   finding of the same id, decided like any other.

`kb todo` lists the steps as `eval:extract`, `eval:run-b`, `eval:judge` and `eval:verdicts`, one at a time. They come
only when there is no other `do` item and no blocking question, and again whenever the records or the documents change
(records added or fixed for a decision excepted). The playbook tells the agent to follow them in order, to run the
blind extraction in a fresh session, and never to decide a finding itself. `kb eval status` shows the last
evaluation: coverage, agreement, open and decided findings, whether it is current, and every decision with the
sentences it silences. `kb eval sample [--n 10]` prints a QC checklist for a pull-request description: records with
their quotes, and unquoted sentences, picked at random but the same for the same commit. A change under `kb/eval/`
needs the intent approvers ("evaluation decisions changed").

`kb/eval/decisions.yaml` must be exactly what `kb eval decide` writes (kb's canonical YAML), so a reviewer sees plain
text in the pull-request diff. `kb check` reports `eval.decisions_invalid` (an error) for a file that is not, and for
each entry that does not stand: one inconsistent with its finding or with the knowledge base. Such an entry silences
nothing.

## Install

Python 3.12 or later and [uv](https://docs.astral.sh/uv/). Either from PyPI or from a pinned git tag:

```sh
uv tool install grounded-kb                                                       # from PyPI
uv tool install 'git+https://github.com/mrashidramzan1996/grounded-kb@v0.4.0'     # a pinned tag
kb version
```

For CI, `kb init --install-spec 'grounded-kb==0.4.0'` (or the pinned git spec above) writes it into the workflow;
without `--install-spec` the CI job only warns.

The generated GitHub setup has two workflows (`kb.yml` and `kb-claims.yml`), so the approval gate never runs pull-request code. `kb-policy` runs
`kb verify --base origin/<target> --skip-tests --comment` on pull requests: approvals, schema, stale context and the
decision guard, with no dependency install and no project test (one updated comment, plus `.kb/cache/status.json` and
`.kb/cache/verify.md` as an artifact; it holds `pull-requests: write`). `kb-claims` (its own workflow, so a review run cannot cancel it into a skipped, passing job) installs your project's test
dependencies, runs the tests and reports claim verification with `kb verify --base origin/<target> --forge none`
(read-only token, no comment), and runs `kb verify --all --forge none` on the default branch. Keep `fetch-depth: 0` so the merge-base
exists. `kb verify --skip-tests` runs no project test: claims that need tests are `unverified` ("tests not run
(--skip-tests)"), never broken.

## Approvals

On a pull request, `kb verify` reports `missing_owner_approval` until the right people approve the head commit:
CODEOWNERS (or, without one, the module owners) for the files changed, `policy.approvers.intent` for changed or stale
requirements, exclusions, features and glossary terms, triage files, evaluation decisions (`kb/eval/`) and baselines (`kb/baseline/`), and `policy.approvers.decisions` for changed decisions, invariants and
components. Numbers in a live feature's claims, title and body must appear in the statements or quotes of its linked
requirements; otherwise `kb check` warns `claim.number_not_in_requirement`. The approval policy
(`policy.approvers`, `teams`, `bots`, module owners, `fail_on` and `mode`) and CODEOWNERS are read from the current tip
of the target branch (`--base`, else the default branch), not from the pull request and not from the merge-base, so a
change cannot loosen the rules it is checked against, not even by branching from an old commit; the Approvals section
of the report says where they came from (`Policy from base main at <sha>.`). `--base` and the pull request's target name a
branch, looked up by exact ref among local and remote-tracking branches only: a tag with the same name (or named
like the branch's full ref), or a local branch and a remote one of that name at different commits, is an error
(exit 2); delete the tag, or pass a full ref such as `refs/remotes/origin/main`. A change to `kb.config.yaml`, to a
CODEOWNERS file (`CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS`) or to any CI workflow (every file under
`.github/workflows/`, not only `kb.yml` and `kb-claims.yml`, and `.gitlab-ci.yml`) also needs the decision approvers: a
new workflow with a job named `kb-policy` that always passes would otherwise satisfy the required check. A rename
counts as a change to both paths. With `kb verify --all` and a pull request, the policy comes from the branch the pull request targets (or
the default branch); a base branch that cannot be found is an error (exit 2). Without a pull request and without a base the working
tree's policy applies. Only when the target branch has no `kb.config.yaml` yet does the change's own policy apply, with
the target branch's CODEOWNERS.

`kb verify` only reports; the forge enforces. These are prerequisites, not options:

- Make `kb-policy` (the job that runs `kb verify --skip-tests`) a required status check on the default branch (GitHub: a
  branch protection rule or ruleset; GitLab: "Pipelines must succeed"), so a pull request cannot merge while it fails.
  A required status check matches by job name alone, so require it from the `kb.yml` workflow itself where the forge
  can (GitHub: a ruleset's "Require workflows to pass before merging" naming `.github/workflows/kb.yml` on the default
  branch), and protect the whole `.github/workflows/` directory with code-owner review (below).
  `kb-claims` runs the project's tests and reports claim verification; it runs pull-request code, so it is not the gate.
- Enable "Require review from Code Owners" (GitLab: code owner approval) and give the CODEOWNERS lines `kb init`
  writes real owners, including the gate's own files (`/kb.config.yaml`, the CODEOWNERS locations, the whole
  `/.github/workflows/` directory and `/.gitlab-ci.yml`): a pull request runs its own copy of the workflows, so only the
  forge's code-owner review stops one that edits a workflow to skip or weaken the check, or adds one that fakes it.
- Restrict who can create tags (GitHub: a tag ruleset; GitLab: protected tags with `*`), so nobody can push a tag
  named like a branch. `kb verify` refuses such a tag (exit 2) rather than trusting it.

To bootstrap, name the approvers on the default branch first: commit `policy.approvers.decisions` (and `intent`) there
before any pull request changes the config. While the default branch names no decision approvers, a pull request that
changes `kb.config.yaml` or CODEOWNERS cannot pass the approval check. In the pull request that adds `kb.config.yaml`, its own policy
applies: name the decision approvers in that change. An approver group that names nobody (`intent: []`) while a pull request changes
the records it approves is reported as an `owner_approval.no_approvers` warning: those changes need no approval.

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

## Upgrading to 0.4.0

A 0.3.x knowledge base still passes `kb check`, with warnings. The new rules are errors only for records drafted in the
current ingest or redraft range.

- Quotes must now align to word and number boundaries: a quote that cuts a word or number in half (for example `seat`
  inside `seats`, or `2` inside `25`) now counts as not found. Quotes shorter than 12 non-space characters are reported
  as `quote.too_short` (a warning, and an error for records drafted in the current ingest or redraft range).
- A triage `none:` entry takes a reason code: `none: <code>[: free text]`, with `heading`, `background`, `contact`,
  `boilerplate`, `example`, `duplicate`, `out-of-scope` or `other`. An entry without one warns `triage.none_uncoded`,
  and `out-of-scope` warns `triage.use_exclusion` (record an `EXCL` record instead). A triage list must name a live
  record that quotes the segment (`triage.not_quoted`).
- Requirements have new fields: `modality` (must, must_not, should or may), `actor`, `category` (REQ-NF only) and
  `origin` (stated or implied). A requirement without them warns `requirement.fields_missing`. Add them when you next
  redraft (`kb redraft <ID>`). An `implied` requirement needs a confirming quote from `docs/clarifications.md`.
- CODEOWNERS: the block an older `kb init` wrote lacks `/kb/exclusions/`, `/kb/sources/*/triage.yaml`, `/kb/eval/` and
  `/kb/baseline/` (for the intent approvers), and names single workflow files where the decision approvers now own the
  whole `/.github/workflows/` directory (a pull request that adds any workflow needs them). A block from before 0.3.1
  also lacks `/.gitlab-ci.yml` and the CODEOWNERS files. `kb init`
  does not run on a set-up repository, so replace your kb block with the one `kb init` writes now (run it in an empty
  scratch repository to see it), or add those lines to yours.
- GitHub CI is now two workflows. `kb.yml` runs the `kb-policy` job (approvals, schema, stale context, the decision
  guard; no project test, no dependency install), and `kb-claims.yml` runs your project's tests for claim
  verification. `kb init` leaves an existing workflow alone, so replace `.github/workflows/kb.yml` with the new template
  (in an empty scratch repository, `kb init --install-spec '<your spec>'` writes both files) and add
  `.github/workflows/kb-claims.yml`. Then make `kb-policy`, not the old `check` job, the required status check.
- Team approvers (`@org/team`) fail closed since 0.3.1: list each team's members under `teams.<name>.members`, or set
  `policy.unreadable_teams: defer` (see [Upgrading to 0.3.1](#upgrading-to-031)).
- The evaluation is new: `kb todo` lists `eval:extract` once the knowledge base is otherwise settled, and
  `kb baseline freeze` now refuses (exit 1) until a current evaluation has no open finding and its dismissals are
  agreed with; `--unevaluated` freezes anyway. See [Evaluate the extraction](#evaluate-the-extraction). Run `kb agents install <name>` to update
  the playbook in `AGENTS.md` (and the Claude Code skill).

## Upgrading to 0.3.1

`kb verify` now reads the approval policy from the target branch's tip, and team approvals fail closed: when the forge
cannot read an `@org/team` approver's members, an approval by one of them no longer counts. The default GitHub Actions
`GITHUB_TOKEN` cannot read organisation team membership, so most GitHub repositories with team approvers will report
`missing_owner_approval` after the upgrade until you do one of these, on the default branch first:

- list each team's members under `teams.<name>.members` in `kb.config.yaml` (forge logins, no `@`);
- give the verify step a token that can read teams (`read:org`, or a GitHub App token with Members: read);
- or set `policy.unreadable_teams: defer` to keep 0.3.0's behaviour (any approval counts, with an
  `owner_approval.deferred` warning, leaving the check to branch protection's required code-owner review).

Also give the decision approvers CODEOWNERS lines for the CI workflow and the CODEOWNERS files themselves
(`/.github/workflows/kb.yml`, `/.github/workflows/kb-claims.yml`, `/.gitlab-ci.yml`, `/CODEOWNERS`, `/.github/CODEOWNERS`, `/docs/CODEOWNERS`; `kb init`
does not rewrite an existing setup), and see the prerequisites under [Approvals](#approvals).

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
| `kb todo [--format json]` | What your coding agent does next (`do` items, most with the command to run) and what it must ask you (`ask` items, with evidence and options). Once a baseline exists, work on the default branch starts with a `branch` item (`git switch -c kb/update-<date>`), and "Freeze baseline vN?" is offered only on the default branch itself, never beside an open evaluation question (a finding, `dismissed` or `categories`); its option starts with committing pending changes and running `kb todo` again. It also lists `report:ingest` / `report:redraft` while a brief's result is not committed, `commit` while `kb/`, `kb.config.yaml` or `docs/clarifications.md` has uncommitted changes, and an `unreadable:` question for a document `kb ingest` cannot read. Exit 0 whatever is listed; `state.clean` is true when there are no errors, no `do` items and no blocking questions. |
| `kb clarify --topic T --text "…" --by @handle [--date YYYY-MM-DD]` | Record a person's answer as a section of `docs/clarifications.md`, stored as a source, and print the segment to quote (`SRC-clarifications#seg-NN`). Refuses emails, secrets, markup, answers over 1500 characters, topics over 200 or containing ` > `, and handles over 64 characters. |
| `kb module add MOD-x --owner <@handle\|TODO>` | Add a module without paths. |
| `kb module paths MOD-x --accept` | Write the paths suggested from `r[impl ID]` markers (or `--set GLOB`). |
| `kb check [--strict] [--changed] [--base REF]` | Run every gate: config, records, quotes, anchors, sources (re-derived from the originals), modules, triage. |
| `kb context --paths F / --module M / --id X` | The live requirements, decisions and invariants governing what you are about to touch. |
| `kb trace <ID>` | The evidence chain of a record, its quotes re-checked. |
| `kb graph [--format json\|mermaid]` | The live records, modules and their links (entity relations, governs, belongs_to) as JSON or a Mermaid diagram. |
| `kb eval extract` | Write the blind brief for a second extraction (run B) to `.kb/cache/eval/extract.md`, clearing the last run B. |
| `kb eval judge` | Align run B with the knowledge base and write the judge brief (`.kb/cache/eval/judge.md`), one item per question. |
| `kb eval report [--write]` | Check the verdicts and print coverage, agreement and the ranked findings; `--write` writes `kb/eval/summary.yaml`. Exit 1 when a verdict is missing or invalid. |
| `kb eval decide F-… --as <decision> --by @handle [--record ID] [--reason "…"]` | Record a person's decision on a finding in `kb/eval/decisions.yaml`, after checking the knowledge base shows it. `kb eval decide dismissed --as ok` agrees with the judge's dismissals; `--as reopen` on a dismissed item makes it an open finding. |
| `kb eval status` | The last evaluation: coverage, agreement, open and decided findings, every item the judge dismissed, whether it is current, and the decisions. |
| `kb eval sample [--n 10]` | A QC checklist for the pull-request description: records with their quotes and unquoted sentences, seeded by HEAD. |
| `kb status` | Per module: how many live requirements are built (`r[impl]`) and tested (`r[verify]`). |
| `kb baseline freeze v1 [--unevaluated]` | Record the agreed requirement set and its evaluation on the default branch's tip (refused while `kb check` reports errors, and with exit 1 while the evaluation is missing, stale or not decided unless `--unevaluated`). |
| `kb agents install claude-code / codex / generic` | Wire the session-start and stop hooks, or a git pre-commit hook, and update the kb block (rules and playbook) in `AGENTS.md`. |
| `kb hook <event>` | The hook entry point the agents call; it blocks only on errors new since the default branch. |

Exit codes: 0 ok, 1 policy failure, 2 usage, config or tool error. `--format json` goes to stdout, messages to stderr.

The walk-through on a sample repository is in [`docs/phases/phase1-demo.md`](docs/phases/phase1-demo.md); the
design is in [`kb_spec.md`](kb_spec.md).
