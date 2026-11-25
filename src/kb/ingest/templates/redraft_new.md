## The rules for new text

The segments under "New text to draft from" have no records yet: they are new, or their text changed and no
record cites them.

1. Draft only from these segments: every quote of a new record cites one of them.
2. Draft one fact per requirement: one `kb/requirements/REQ-*.yaml` file per testable statement
   (`REQ-F-…` functional, `REQ-NF-…` non-functional), linked to a module or feature. Draft a passage that
   records a choice as an ADR, and a must-keep rule as an INV, each with quotes.
3. Quote word for word from the segment: its `text` must appear in that segment exactly as written.
3b. Give every requirement `modality` (must, must_not, should, may), `actor` (who it binds), `category` for
   REQ-NF, and `origin` (stated, or implied when the document implies it without saying it). An implied
   requirement must later be confirmed by the user.
4. Start from the next free IDs above and count up. Never reuse an ID.
5. Never duplicate an existing requirement: check the list under "Existing requirements". When a segment
   only restates one, draft nothing from it and triage it `"none: duplicate: restates <ID>"`.
6. Never invent numbers, targets or details. When a line is vague (for example "must be fast"), draft it
   as written and flag it `flags: [vague]` instead of making up a target.
7. Write a triage entry for every segment below, in `kb/sources/SRC-…/triage.yaml`: the IDs drafted from
   it (at least one of the named records must quote that segment), or `"none: <code>: <reason>"` with a code from heading, background,
   contact, boilerplate, example, duplicate, out-of-scope, other. Update the entry a changed segment already has.
8. Set `drafted_by: "agent:<your name> via kb redraft brief $version"` on every record you draft.

## Existing requirements

$requirements

## New text to draft from

Each segment is one fenced block. Its first line, `heading: …`, is the document's heading path for
that segment; the rest is the segment text to quote from.

$segments
