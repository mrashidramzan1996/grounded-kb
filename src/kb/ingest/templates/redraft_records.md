## The rules for redrafting

1. Edit only the records listed under "Records to redraft". Where a record shows `before` and `after` text, the
   cited sentence changed from the first to the second; redraft the record to match the `after` text.
2. Keep every record's ID and file.
3. Quote word for word from the current segment text below.
3b. Keep or add the four fields when you edit a record: keep or add the four fields (`modality`, `actor`, `category`
   for REQ-NF, `origin`) so that a record redrafted from 0.3 is not rejected. `modality` is `must` (must, shall,
   required, is to), `must_not` (must not, shall not, never, no X is, prohibited, cannot), `should` (should,
   recommended) or `may` (may, can, optional); a declarative statement of what the system does is `must`.
   `origin` is `stated`, or `implied` when the document only implies it, which the user must later confirm.
4. Never invent numbers. Every number in a statement must be in a quote.
5. Remove `vague` only when the statement now states a measurable target whose number is in a quote
   from the record's own segments or the new text below. If the document is still vague, keep the flag
   and leave the statement as it is.
6. A requirement or an exclusion (EXCL) is edited in place; an exclusion has no `modality`, `actor`, `category` or
   `origin`. A live ADR or INV is never edited: draft a new record with
   `supersedes: <id>`, using the next free ID.
7. If the cited text is gone and no replacement exists, set `status: rejected` on the requirement or exclusion.
   For an ADR or INV, report it and do not draft.
8. Update `triage.yaml` entries for segments you now cite. If a record cites a new `SRC-clarifications` segment,
   add or update the triage entry for exactly that segment.
9. Set `drafted_by: "agent:<your name> via kb redraft brief $version"` on what you change.

## Records to redraft

$records
