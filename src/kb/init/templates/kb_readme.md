# kb: product knowledge base

This directory holds the repository's knowledge base: product intent, decisions and invariants, each backed by a verbatim quote from a stored source document. It is managed with the `kb` CLI; edit records by hand only as described below. The working rules for coding agents are in [`AGENTS.md`](../AGENTS.md); they are not repeated here.

## Workflow

Commit document edits together with `kb source sync`, before running `kb ingest`.

- `kb source add <doc>` / `kb source sync` store and re-read product documents under `kb/sources/`.
- `kb ingest` prepares a drafting brief; drafted records are reviewed in a pull request.
- `kb check` runs every gate; `kb context` and `kb trace` read the KB.

## Agent-driven mode

You can leave all of this to your coding agent: tell it, for example, "Set up the knowledge base from the documents in docs/." The playbook in `AGENTS.md` has it run `kb todo`, do every `do` item itself, and ask you every `ask` item (approvers, module owners, a target for a vague line, a secret in a document, freezing the baseline) in one message. It records each new fact you give with `kb clarify`, which appends it to `docs/clarifications.md` as a source the records can quote. kb never answers a question for you, and approval is still a merged pull request.

## When a document changes

1. Edit the document, then run `kb source sync`. It names the records that cite changed text and the new or changed segments that have no records yet.
2. Before committing, run the `kb redraft` it suggests (`--changed`, `--new`, or both) and ask your coding agent to follow `.kb/cache/redraft/brief.md`. If you already committed the sync, run `kb redraft --changed --base <commit before it>`. Only those records are redrafted; ids are kept, and an ADR or INV changes only through a new record that supersedes it. New records are drafted only from the new or changed text, with the next free ids, and every new segment gets a triage entry.
3. Run `kb redraft --report`, then `kb check`, and commit the document, the sync and the redraft together.

After the baseline, this is a pull request: the intent approvers sign off on stale requirements. `kb redraft --report` is a local check; `kb check` and `kb verify` in CI and the pull-request review are what decide.

## CI

`kb init` writes a CI job (`.github/workflows/kb.yml` or `.gitlab-ci.yml`) that runs `kb verify` on every pull request
and `kb verify --all` on the default branch. It installs nothing until you set its install spec, `KB_INSTALL_SPEC`; until then the job
only prints a warning. Use a spec you control and pin it:

- a tagged git URL, e.g. `git+https://github.com/<owner>/grounded-kb@<tag>`, or
- the published package with an exact version (`grounded-kb==<version>`), once its owner publishes it.

On GitHub the pull-request job posts (and updates) one comment and uploads `.kb/cache/status.json` and `.kb/cache/verify.md`
as an artifact; the comment needs `permissions: pull-requests: write` (the template sets it). GitLab prints the Markdown to
the job log and keeps both files as artifacts. Install your project's test dependencies before the verify step, because
`kb verify` runs the linked tests with the project's own `python`. Keep `fetch-depth: 0` (`GIT_DEPTH: "0"` on GitLab):
a shallow clone has no merge-base and `kb verify --base` exits 2. The GitHub job checks out the PR head
(`ref: ${{ github.event.pull_request.head.sha }}`), not GitHub's test-merge commit, so the stale-context check sees
the branch's real fork point; it also re-runs when a review is submitted or dismissed, so an approval clears
`missing_owner_approval` without a manual re-run.

Never use an unpinned package name: anyone could publish under a name nobody has claimed. `kb init --install-spec <spec>`
writes the spec for you.

## Running tests

`kb verify` runs the tests a claim links to, per module, with `modules[].tests` in `kb.config.yaml`:

```yaml
modules:
  - id: MOD-billing
    owner: "@acme/billing"
    tests:
      preset: pytest            # or: run: "pytest {tests} --junitxml={junit}" (exactly one of preset / run)
      id_format: "{path}::{name}"   # optional: how a test id is written on the command line
      timeout_seconds: 900      # optional
```

The `pytest` preset runs `python -m pytest` with the project's own `python` from `PATH` (not kb's interpreter), so
activate the project's environment first. A custom `run` must contain `{tests}` (expanded to one argument per test, with
no shell involved) and `{junit}` (a JUnit XML report path the command must write). A failing test is re-run twice; one that
then passes is reported as flaky.

## Layout

| Path | Holds |
| --- | --- |
| `kb/sources/SRC-<name>/` | Stored documents: `source.yaml`, `text.md`, `segments.jsonl`, `triage.yaml`. Written by `kb`; never edit. |
| `kb/requirements/REQ-*.yaml` | Requirements. |
| `kb/exclusions/EXCL-*.yaml` | Scope exclusions: what the documents put out of scope. |
| `kb/features/FEAT-*.md` | Features: YAML front matter plus a Markdown body. |
| `kb/decisions/ADR-*.md` | Architecture decisions. |
| `kb/invariants/INV-*.md` | Invariants. |
| `kb/glossary/TERM-*.yaml` | Glossary terms. |

The file name is the record id. Records hold only human-owned fields; verification state, approvals and whether a record is superseded are derived by `kb`, never stored.

## Record formats

Requirement (`REQ-F-001.yaml`):

```yaml
id: REQ-F-001
statement: A removed seat stays active until the end of the billing cycle.
evidence:
  mode: all            # all | any
  quotes:
    - source: SRC-pricing-spec-v3#seg-03
      text: A removed seat stays active until the end of the billing cycle.   # verbatim
features: [FEAT-001]   # optional
modules: [MOD-billing] # optional
flags: []              # optional: vague | conflicting | untestable
modality: must         # must | must_not | should | may, as the quote says it
actor: the billing system   # who the rule binds, plain text, at most 60 characters
category: performance  # REQ-NF only: performance | security | privacy | availability | reliability | usability | compliance | compatibility | maintainability | portability
origin: stated         # stated | implied (implied needs a confirming quote from SRC-clarifications)
```

`kb check` warns `requirement.fields_missing` (modality, actor or category absent), `requirement.modality_mismatch` (the quote's rule word differs from `modality`), and `requirement.implied_unconfirmed`. For a requirement drafted in the current ingest or redraft range, the first two are errors.

Exclusion (`EXCL-001.yaml`): a statement the documents put out of scope, quoted like a requirement.

```yaml
id: EXCL-001
statement: Mobile apps are out of scope for the first release.
evidence:
  quotes:
    - source: SRC-pricing-spec-v3#seg-09
      text: Mobile apps are out of scope for the first release.   # verbatim
modules: [MOD-billing] # optional
features: [FEAT-001]   # optional
```

An exclusion has no `modality`, `actor`, `category` or `origin`. Its quotes are checked like a requirement's, the sentences it quotes count as covered, `kb context` lists it under "Out of scope" for its modules and features, and `kb redraft` can redraft it in place. Record an out-of-scope segment as an exclusion rather than triaging it `none: out-of-scope`.

Feature (`FEAT-001.md`): front matter with `id`, `title`, `module`, `requirements`, `claims` (each `id`, `claim`, and optional `requirement`, `code`, `test`); the Markdown body follows.

Decision (`ADR-0001.md`): front matter with `id`, `title`, `governs` (module or record ids), `evidence`, optional `decided_by`, `revisit_when`, `supersedes`; the reasoning is the body. An accepted decision is never edited: to change it, add a new decision whose `supersedes` names it.

Invariant (`INV-001.md`): front matter with `id`, `statement`, `governs`, `evidence`, optional `tests`, `supersedes`; optional body. Like a decision, an accepted invariant is never edited; supersede it.

A decision or invariant drafted from documents (`drafted_by` set) must quote them in `evidence`; without it `kb check` reports the error `evidence.missing`. One a person records without a document is a warning. Every number in a statement, or in a decision's title and body, must appear in one of its quotes. For a record drafted in the current ingest or redraft range, a missing number is an error (`statement.number_not_in_quote`), otherwise a warning. A number includes ISO-style names such as `ISO-27001`.

Quotes must be verbatim and aligned to word and number boundaries: `seat` does not match inside `seats`, nor `2` inside `25`. A quote shorter than 12 non-space characters is rejected (`quote.too_short`). `evidence.mode` is `all` by default (every quote must be found); with `mode: any` an unfound quote is only a warning (`quote.not_found`) while at least one quote is found, and an error when none is.

Approvers by directory: the intent approvers cover `kb/requirements/`, `kb/exclusions/`, `kb/features/`, `kb/glossary/` and every `kb/sources/SRC-<name>/triage.yaml`; the decisions approvers cover `kb/decisions/`, `kb/invariants/` and `kb/components/`. A number in a feature's claim must appear in the requirement the claim links (or, without one, the feature's requirements), statement or quote; a number in its title or body must appear in one of its linked requirements. Otherwise `kb check` warns `claim.number_not_in_requirement`.

Term (`TERM-seat.yaml`): `id`, `name`, `aliases`, `definition`.

Every record may also carry `status` (`live` or `rejected`), `owner`, `visibility` and `drafted_by`.

## Triage and coverage

Every segment of a stored document gets an entry in its source's `triage.yaml`: the ids of the records drafted from it, or `none:` with a reason code and optional free text.

```yaml
segments:
  seg-01: "none: heading: document title"
  seg-02: [REQ-F-001, REQ-F-002]
  seg-03: "none: background"
```

- The reason codes are `heading`, `background`, `contact`, `boilerplate`, `example`, `duplicate`, `out-of-scope` and `other`, written `none: <code>[: free text]`. A `none:` reason without a code is the warning `triage.none_uncoded`. `out-of-scope` is the warning `triage.use_exclusion`: record the exclusion as an `EXCL` record (`kb/exclusions/EXCL-NNN.yaml`) instead.
- A list must name at least one live record with a quote found in that segment: a requirement, decision or invariant, or a feature whose linked requirement (or a claim's) has one. A quote that is not found, too short, or on a rejected or superseded record does not count. Otherwise `kb check` warns `triage.not_quoted`. Glossary terms and components in a list are not checked yet.
- `kb check` reports both as warnings, so an older knowledge base keeps passing. The ingest and redraft reports make them errors when the issue is new since the base. That covers an entry written or changed in the range, and also an unchanged entry whose record stopped quoting the segment, which the ingest report lists under "Caused by this change". An entry committed before the range with the same problem stays a warning. One exception: in a redraft, a list that stops being quoted only because a selected record now quotes another segment stays a warning. For a `triage.yaml` that existed at the base, Flagged shows only the warnings that are new since the base; a triage file written in the range shows all of them.
- A change to a `triage.yaml` needs the intent approvers (`triage of SRC-<name> changed`): a `none:` entry says that a part of a document holds no product intent.

kb splits each segment into sentences and marks the rule-like ones (must, shall, should, may, limits, and quantities: a number with a currency, a percentage or a unit such as days or seats; a bare date, year, page or section number is not one). `kb check` warns `sentence.uncovered` for a rule-like sentence that no live requirement, decision or invariant quotes, `triage.none_has_rules` for a segment triaged `none:` that holds one, and `quote.multiple_rules` for a quote that spans two or more rule-like sentences (split it into one record per rule).

## Code markers

Put `r[impl REQ-F-001]` in a comment next to the code that implements a requirement, and `r[verify REQ-F-001]` in its test. `kb check` verifies that every marker names a real record.
