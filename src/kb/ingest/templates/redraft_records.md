## The rules for redrafting

1. Edit only the records listed under "Records to redraft".
2. Keep every record's ID and file.
3. Quote word for word from the current segment text below.
4. Never invent numbers. Every number in a statement must be in a quote.
5. Remove `vague` only when the statement now states a measurable target whose number is in a quote
   from the record's own segments or the new text below. If the document is still vague, keep the flag
   and leave the statement as it is.
6. A requirement is edited in place. A live ADR or INV is never edited: draft a new record with
   `supersedes: <id>`, using the next free ID.
7. If the cited text is gone and no replacement exists, set `status: rejected` on the requirement.
   For an ADR or INV, report it and do not draft.
8. Update `triage.yaml` entries for segments you now cite.
9. Set `drafted_by: "agent:<your name> via kb redraft brief $version"` on what you change.

## Records to redraft

$records
