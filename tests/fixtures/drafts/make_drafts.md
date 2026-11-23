# How the acme-billing drafts were made

`acme-billing/` simulates what a coding agent writes after following the `kb ingest` brief for
`tests/fixtures/repos/acme-billing`. `tests/ingest/test_report.py` copies it over an ingested repo
(`apply_drafts`) and checks the gated report.

## Where the segment IDs come from

Every `source:` in the drafts and every key in the `triage.yaml` files is a real segment ID produced
by the segmenter (Task 8) for the three docs. They were obtained like this:

1. Copy `tests/fixtures/repos/acme-billing` to a scratch directory and write the `ACME_CONFIG` from
   `tests/conftest.py` to its `kb.config.yaml`.
2. Run `add_source` on each file under `docs/` (or `uv run kb -C <scratch> ingest docs/`).
3. For each source, print every segment: `KbRepo.load(scratch).segments(src)` gives id and heading,
   and `KbRepo.source_text(src)[seg.start:seg.end]` gives the masked text. (The `kb ingest` brief at
   `.kb/cache/ingest/brief.md` lists exactly this.)
4. Copy each quote word for word from that segment text.

At the time of writing the segments were:

| Source | Segment | Heading (last part) | Used by |
|---|---|---|---|
| SRC-architecture-notes | seg-02 | Decision 1: Session storage | ADR-0001 |
| SRC-architecture-notes | seg-03 | Decision 2: Money representation | ADR-0002 |
| SRC-architecture-notes | seg-04 | Rule: Session expiry | INV-001 |
| SRC-architecture-notes | seg-06..08 | Services (one table row each) | modules MOD-billing, MOD-sessions, MOD-web |
| SRC-architecture-notes | seg-09 | Notes pasted from the review chat (guard: injection) | none |
| SRC-pricing-spec-v3 | seg-07 | 2. Plans ("Annual billing is available ...") | REQ-F-005 (invented quote) |
| SRC-pricing-spec-v3 | seg-09 | 3.1 Adding seats | REQ-F-003 |
| SRC-pricing-spec-v3 | seg-10 | 3.2 Removing seats | REQ-F-001, REQ-F-002 |
| SRC-pricing-spec-v3 | seg-12 | 5. Non-functional requirements | REQ-NF-001 (vague), REQ-NF-002 |
| SRC-product-brief | seg-03 | 2. Goals | REQ-F-004 |
| SRC-product-brief | seg-06 | 5. Contact | left untriaged on purpose |

Every other segment is triaged as `none: <reason>`.

## Modules

The proposed modules travel as the drafts' `kb.config.yaml`, which replaces the repo's config: it is
`ACME_CONFIG` plus MOD-billing, MOD-sessions and MOD-web, with no paths. (An agent would run
`kb module add MOD-<name> --owner … --risk …`, which writes the same config entries.)

## If segmentation changes

Re-run steps 1-3, update the `source:` lines, the quotes and the `triage.yaml` keys to the new IDs,
and update the table above. The report test pins the expected outcome (only REQ-F-005 rejected,
REQ-NF-001 flagged vague, only the Contact segment untriaged), so a stale fixture fails loudly.
