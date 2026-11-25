# Phase 2 live demo (to be run by the user)

Status: **to be run by the user.** Nothing below has been run against `/Users/dev/projects/acme-billing`. The same
behaviour is proved automatically on a copy of the `kb_valid` fixture by `tests/e2e/test_phase2.py`, and measured over
20 seeded changes by `eval/staleness.py`. Run the steps by hand, then fill in the result tables.

Start from the end of the Phase 1 demo (`docs/phases/phase1-demo.md`): the sample repo has `kb.config.yaml`, the three
sources, the drafted records, `MOD-billing` with `paths: [services/billing/**]`, and a clean `git status` on `main`.
The IDs below (`REQ-F-002` for "No refund is given when a seat is removed", `REQ-F-001` for "A removed seat stays active
until the end of the billing cycle") are the usual numbering. If your agent numbered them differently, use your IDs.

## 0. Install the wheel and a Python with pytest

```sh
cd /Users/dev/projects/kb
uv build --wheel --out-dir /tmp/kb-dist
uv tool install --force /tmp/kb-dist/grounded_kb-0.4.0-py3-none-any.whl
kb verify --help                # lists --base, --all, --format, --forge, --comment, --strict
```

The pytest preset runs `python -m pytest` with the `python` found on `PATH`, not kb's own interpreter. The sample repo
has no Python environment of its own, so make one outside the repo, which keeps it out of the change set:

```sh
uv venv /tmp/acme-venv && uv pip install --python /tmp/acme-venv pytest
export PATH=/tmp/acme-venv/bin:$PATH
python -m pytest --version      # prints the pytest version
```

## 1. Give MOD-billing a test command

```sh
cd /Users/dev/projects/acme-billing
git checkout main
```

In `kb.config.yaml`, add `tests` to the `MOD-billing` entry, keeping the indentation of its other keys:

```yaml
  - id: MOD-billing
    owner: '@acme/billing'
    tests:
      preset: pytest
    risk: high
    paths:
      - services/billing/**
```

```sh
kb check                        # exit 0; an unknown key under tests: would be an error
git add -A && git commit -m "pytest preset for MOD-billing"
```

## 2. Write the code, its test and a claim, with markers

Replace whatever Phase 1 step 7 left in these two files:

`services/billing/seats.py`

```python
def remove_seat(account, seat):  # r[impl FEAT-001.C1]
    """Deactivate the seat at the end of the billing cycle. Return the refund, in cents: none."""
    account.pending_removals.append(seat)
    return 0
```

`tests/test_seats.py`

```python
from types import SimpleNamespace

from services.billing.seats import remove_seat


# r[verify FEAT-001.C1]
def test_no_refund_on_removal():
    account = SimpleNamespace(pending_removals=[])
    assert remove_seat(account, "seat-1") == 0
    assert account.pending_removals == ["seat-1"]
```

`kb/features/FEAT-001.md`: the claim ties the requirement to a code anchor and a test.

```markdown
---
id: FEAT-001
title: Seat removal
module: MOD-billing
requirements:
- REQ-F-002
claims:
- id: FEAT-001.C1
  claim: Removing a seat issues no refund.
  requirement: REQ-F-002
  code: services/billing/seats.py#remove_seat
  test: tests/test_seats.py::test_no_refund_on_removal
---
Removing a seat leaves it active until the end of the billing cycle and issues no refund.
```

## 3. Verify everything

```sh
kb check                        # exit 0 (warnings only)
kb verify --all
kb verify --all --format markdown   # the PR comment body; the claims table shows status, level and notes
git status --short              # the test run leaves nothing behind (status.json and verify.md live in .kb/cache/)
git add -A && git commit -m "seat removal with a claim"
```

Expect: `FEAT-001.C1` and `REQ-F-002` verified at E2 (they don't appear in the text output, which lists only what is not
verified; the Markdown table shows them). The requirements with no code show as `not_built ... E0`. An invariant that
lists no tests is `broken ... E0: invariant lists no tests`, and it blocks: Phase 2 requires every invariant to have a
passing test. The last line reads `<n> broken, 0 stale, 0 unverified, 2 verified, 0 built, <n> not_built; <n> blocking (mode: warn-only)`, with
exit 0, because `warn-only` never fails.

**Record what you see:**

| Record | Status | Level |
|---|---|---|
| FEAT-001.C1 | _ | _ |
| REQ-F-002 | _ | _ |
| INV-001 (or your invariant) | _ | _ |
| Summary line | _ | |

## 4. Refactor: the claim stays verified, with the note `changed`

Each experiment runs on its own branch off `main`. Without `--base`, `kb verify` compares against the merge-base with
the default branch. If your default branch is not `main`, pass `--base <branch>`.

```sh
git checkout -b demo/refactor main
```

In `services/billing/seats.py`, replace `account.pending_removals.append(seat)` with two lines:

```python
    pending = account.pending_removals
    pending.append(seat)
```

```sh
git commit -am "refactor remove_seat"
kb verify                       # only the affected claim and its requirement are checked; nothing broken
kb verify --format markdown     # | FEAT-001.C1 | verified | E2 | changed |  |
```

Optional, a format-only change on its own branch: `git checkout -b demo/format main`, write
`account.pending_removals.append( seat )` with the extra spaces, add a comment line to the body, then
`git commit -am "format only"` and `kb verify --format markdown`. The claim is checked (the file changed), but it has
no `changed` note: formatting, comments and docstrings are not part of the fingerprint. `uvx ruff format services/`
then puts the spacing back, again with no note.

| Branch | FEAT-001.C1 status | Level | Notes | Exit code |
|---|---|---|---|---|
| demo/refactor | _ | _ | _ | _ |
| demo/format (optional) | _ | _ | _ | _ |

## 5. Rename: the anchor no longer resolves, so the claim is broken

```sh
git checkout -b demo/rename main
sed -i '' 's/remove_seat/deactivate_seat/g' services/billing/seats.py tests/test_seats.py
git commit -am "rename remove_seat"
kb verify                       # broken FEAT-001.C1 E0: anchor services/billing/seats.py#remove_seat no longer resolves
                                # broken REQ-F-002 E1: claim FEAT-001.C1 is broken; exit 0 (warn-only)
kb verify --strict; echo "exit $?"   # exit 1: a broken claim blocks
```

The tests still pass. The claim breaks because its `code:` anchor names a function that no longer exists, and the fix
is to update the claim. `kb check` reports the same thing as `error anchor.unresolved`.

| Branch | FEAT-001.C1 | REQ-F-002 | Exit (warn-only) | Exit (--strict) |
|---|---|---|---|---|
| demo/rename | _ | _ | _ | _ |

## 6. Edit the spec: the requirements citing the reworded segment go stale

The spec is a PDF made by `examples/acme-billing/make_docs.py`. Regenerate it with section 3.2 reworded but both quotes
kept, and copy only the PDF over. Don't copy the generated `.docx` files: in Phase 1 you removed the secret from the
architecture notes by hand.

```sh
git checkout -b demo/spec main
sed 's/p("A removed seat stays active until the end of the billing cycle. No refund is given.")/p("Removal takes effect at the next renewal. A removed seat stays active until the end of the billing cycle. No refund is given.")/' \
  /Users/dev/projects/kb/examples/acme-billing/make_docs.py > /tmp/make_docs_v2.py
grep -c "next renewal" /tmp/make_docs_v2.py   # 1
(cd /Users/dev/projects/kb && uv run --with fpdf2 --with python-docx python /tmp/make_docs_v2.py /tmp/acme-docs-v2)
cp /tmp/acme-docs-v2/pricing-spec-v3.pdf docs/pricing-spec-v3.pdf
kb source sync                  # SRC-pricing-spec-v3: 12 segments ...
git add -A && git commit -m "reword 3.2 Removing seats"
kb verify
```

Expect: `stale REQ-F-001 ...` and `stale REQ-F-002 E2: cited segment SRC-pricing-spec-v3#seg-10 changed; needs intent
approvers' review` (the segment under "3.2 Removing seats". Its number is whatever `kb trace REQ-F-002` shows). Because
the quotes are still found, the status is `stale`, not `broken`. To see `broken`, reword so that "No refund is given."
disappears.

Now check owner approval with a local forge file that stands in for the pull request:

```sh
printf '{"pr": {"number": 1, "head_sha": "%s", "author": "you"}, "reviews": [], "teams": {}, "comments": []}\n' \
  "$(git rev-parse HEAD)" > /tmp/acme-forge.json
kb verify --forge local:/tmp/acme-forge.json
# error missing_owner_approval - -: @acme/product has not approved <sha> (requirement REQ-F-001 is stale, requirement REQ-F-002 is stale)
```

To clear it, add an approving review on the head commit by a member of `@acme/product`, for example
`"reviews": [{"login": "paula", "state": "APPROVED", "commit_id": "<head sha>", "submitted_at": "2026-11-24T00:00:00Z"}],
"teams": {"@acme/product": ["paula"]}`, and run the command again.

| Branch | REQ-F-001 | REQ-F-002 | Cited segment | Owners missing (local forge) |
|---|---|---|---|---|
| demo/spec | _ | _ | _ | _ |

Clean up with `git checkout main && git branch -D demo/refactor demo/rename demo/spec` (and `demo/format` if you made it).

## 7. Staleness eval (optional, deterministic)

```sh
cd /Users/dev/projects/kb
uv run python eval/staleness.py   # 20 changes: precision …, recall … -> eval/results/staleness.json
```

This eval runs on the `kb_valid` fixture, not on the sample repo. It needs no network and calls no model. Any
mislabelled change is listed on stderr.

| Changes | Precision | Recall | Mislabelled |
|---|---|---|---|
| 20 | _ | _ | _ |
