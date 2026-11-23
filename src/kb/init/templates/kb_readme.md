# kb: product knowledge base

This directory holds the repository's knowledge base: product intent, decisions and invariants, each backed by a verbatim quote from a stored source document. It is managed with the `kb` CLI; edit records by hand only as described below. The working rules for coding agents are in [`AGENTS.md`](../AGENTS.md); they are not repeated here.

## Workflow

Commit document edits together with `kb source sync`, before running `kb ingest`.

- `kb source add <doc>` / `kb source sync` store and re-read product documents under `kb/sources/`.
- `kb ingest` prepares a drafting brief; drafted records are reviewed in a pull request.
- `kb check` runs every gate; `kb context` and `kb trace` read the KB.

## Layout

| Path | Holds |
| --- | --- |
| `kb/sources/SRC-<name>/` | Stored documents: `source.yaml`, `text.md`, `segments.jsonl`, `triage.yaml`. Written by `kb`; never edit. |
| `kb/requirements/REQ-*.yaml` | Requirements. |
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
```

Feature (`FEAT-001.md`): front matter with `id`, `title`, `module`, `requirements`, `claims` (each `id`, `claim`, and optional `requirement`, `code`, `test`); the Markdown body follows.

Decision (`ADR-0001.md`): front matter with `id`, `title`, `governs` (module or record ids), optional `evidence`, `decided_by`, `revisit_when`, `supersedes`; the reasoning is the body. An accepted decision is never edited: to change it, add a new decision whose `supersedes` names it.

Invariant (`INV-001.md`): front matter with `id`, `statement`, `governs`, optional `evidence`, `tests`, `supersedes`; optional body.

Term (`TERM-seat.yaml`): `id`, `name`, `aliases`, `definition`.

Every record may also carry `status` (`live` or `rejected`), `owner`, `visibility` and `drafted_by`.

## Code markers

Put `r[impl REQ-F-001]` in a comment next to the code that implements a requirement, and `r[verify REQ-F-001]` in its test. `kb check` verifies that every marker names a real record.
