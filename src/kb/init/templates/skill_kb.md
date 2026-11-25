---
name: kb
description: Set up or update the product knowledge base (kb) from documents, resolve its to-do list and ask the user only for decisions. Use when the user mentions requirements, the knowledge base, kb, documents in docs/, or a changed or new document.
---
<!-- written by kb -->
<!-- `kb agents install claude-code` rewrites this file; remove the line above to keep your own edits. -->

# Working with the knowledge base

You do the kb work; the user only decides. `kb todo` separates the two: `do` items are yours, `ask` items are the user's.

{{playbook}}

## Example exchange

After doing the `do` items, the agent asks every `ask` item in one message:

> Two decisions for the knowledge base:
>
> 1. REQ-NF-001 says "The page should load quickly." but gives no measurable target. What should it be?
>    evidence: `docs/requirements.md`, page 2, `Performance`: `The page should load quickly.`
>    a. Give a measurable target (a number and a unit)
>    b. Keep it flagged for now
>    c. It is not a real requirement
> 2. Who approves changes to requirements? Give a forge handle such as @org/team or @user.
>    a. Give a handle

User: "1a, 2a: under 2 seconds, @org/product"

The agent records both facts first (`kb clarify --topic "page load target" --text "The page loads in under 2 seconds." --by <the user's handle>`, and the same for the approver), does what each chosen option's `then` says, and runs `kb todo` again. It does not ask either question again in this session.

## Rules

{{rules}}
