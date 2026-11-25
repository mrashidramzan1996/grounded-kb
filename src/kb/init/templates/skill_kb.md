---
name: kb
description: Set up or update the product knowledge base (kb) from documents, resolve its to-do list and ask the user only for decisions. Use when the user mentions requirements, the knowledge base, kb, documents in docs/, or a changed or new document.
---
<!-- written by kb -->
<!-- `kb agents install claude-code` rewrites this file; remove the line above to keep your own edits. -->

# Working with the knowledge base

You do the kb work; the user only decides. `kb todo` separates the two: `do` items are yours, `ask` items are the user's.

{{playbook}}

## Evaluating the extraction

Once nothing else is left to do, `kb todo` lists the evaluation's `eval:` items, one at a time:

- `eval:extract`: run `kb eval extract`.
- `eval:run-b`: start a fresh session or subagent and have it follow `.kb/cache/eval/extract.md`. It must not read
  the existing records under `kb/`, the triage files or `.kb/cache/ingest/`; it writes only under
  `.kb/cache/eval/run-b/`. Then run `kb eval judge`.
- `eval:judge`: run `kb eval judge`.
- `eval:verdicts`: have another fresh session or subagent follow `.kb/cache/eval/judge.md`, judging each item on its
  own and flagging whenever it is unsure. It writes `.kb/cache/eval/verdicts.yaml`. Then run
  `kb eval report --write` and commit `kb/eval/summary.yaml`.

Each finding the report keeps is then an `ask` item (`finding:F-…`), and an open finding blocks the freeze. Put them
to the user with the other questions. Do what the chosen option's `then` says: for "Add it as a new record", write
the record, run `kb ingest --report`, then run `kb eval decide F-… --as added --record <new id> --by <their handle>`.
Commit `kb/eval/decisions.yaml` with the records. Never run `kb eval decide` for an answer the user did not give.
After the baseline, paste the output of `kb eval sample` into each pull request's description, so the reviewer can
check a few records against their quotes.

## Example exchange

After doing the `do` items (and committing once `kb ingest --report` passed), the agent asks every `ask` item in one
message, and asks who is answering when it does not know:

> Two decisions for the knowledge base. Who answered? Please give your forge handle too.
>
> 1. REQ-NF-001 says "The page should load quickly." but gives no measurable target. What should it be?
>    evidence: `docs/requirements.md`, page 2, `Performance`: `The page should load quickly.`
>    a. Give a measurable target (a number and a unit)
>    b. Keep it flagged for now
>    c. It is not a real requirement
> 2. Who approves changes to requirements? Give a forge handle such as @org/team or @user.
>    a. Give a handle

User: "1a, 2a: under 2 seconds, @org/product. I am @jane."

The agent answers the approver question first: it writes `@org/product` into kb.config.yaml at
`policy.approvers.intent` and commits kb.config.yaml on its own. Then it records the target
(`kb clarify --topic "page load target" --text "The page loads in under 2 seconds." --by @jane`), runs
`kb redraft REQ-NF-001 --new` (one run naming every answered ID), follows the brief, runs `kb redraft --report` and
commits once it passes. It runs `kb todo` again and does not ask either question again in this session. It never
answers a question itself, and never takes `--by` from git config.

## Rules

{{rules}}
