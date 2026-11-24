"""Staleness eval: 20 seeded changes to the kb_valid fixture, each labelled by `kb verify` and scored.

Deterministic, offline, no model calls:

    uv run python eval/staleness.py            # writes eval/results/staleness.json

Each change runs in its own throwaway git copy of tests/fixtures/kb_valid (the pytest preset on MOD-billing): the
fixture is committed on `main`, the change is committed on a branch, and `run_verify(root, base="main", all_=False,
forge=None)` derives the verdicts. Linked tests run with `python -m pytest` from PATH, as `kb verify` does (`uv run`
puts the project's interpreter first).

Every claim and requirement gets one label per change, from {changed, stale, broken, none}:
  broken: status broken; stale: status stale; changed: the note `changed`; none: anything else, or not affected.
Each change lists the labels it expects; every claim or requirement it does not list expects `none`.
A predicted label other than `none` that equals the expected label for the same id is a true positive:
  precision = true positives / predicted labels other than none   (1.0 when nothing is predicted)
  recall    = true positives / expected labels other than none    (1.0 when nothing is expected)
The results file is {"changes", "precision", "recall", "per_change"} with sorted keys and no paths or times; each
entry of per_change also names the claims and requirements `kb verify` checked (`checked`).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kb.config.load import load_config
from kb.records.repo import KbRepo
from kb.sources.store import sync_sources
from kb.verify.run import run_verify

HERE = Path(__file__).parent
FIXTURE = HERE.parent / "tests/fixtures/kb_valid"
RESULTS = HERE / "results/staleness.json"
LABELS = ("changed", "stale", "broken", "none")
SEATS = "services/billing/seats.py"
SEAT_TEST = "tests/test_seats.py"
SPEC = "docs/pricing-spec-v3.md"
SEG_02 = "Adding a seat mid-cycle is charged pro-rata for the remaining days of the billing cycle."
SEG_03 = "A removed seat stays active until the end of the billing cycle. No refund is given."
GIT_ENV = {
    "GIT_AUTHOR_NAME": "kb-eval", "GIT_AUTHOR_EMAIL": "kb-eval@example.invalid",
    "GIT_COMMITTER_NAME": "kb-eval", "GIT_COMMITTER_EMAIL": "kb-eval@example.invalid",
}


# --- edits ---------------------------------------------------------------------------------------

Edit = Callable[[Path], None]


def replace(rel: str, old: str, new: str) -> Edit:
    def apply(root: Path) -> None:
        path = root / rel
        text = path.read_text()
        if old not in text:
            raise ValueError(f"{rel} does not contain {old!r}")
        path.write_text(text.replace(old, new, 1))

    return apply


def write(rel: str, text: str) -> Edit:
    def apply(root: Path) -> None:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)

    return apply


def delete(rel: str) -> Edit:
    return lambda root: (root / rel).unlink()


def move(old: str, new: str) -> Edit:
    return lambda root: (root / old).rename(root / new)


def reword(old: str, new: str) -> Edit:
    """Edit the Markdown original, then `kb source sync` the stored source."""

    def apply(root: Path) -> None:
        replace(SPEC, old, new)(root)
        sync_sources(root, load_config(root / "kb.config.yaml"))

    return apply


def seats_body(body: str, signature: str = "def remove_seat(account, seat):") -> Edit:
    """services/billing/seats.py with remove_seat's docstring and marker kept and `body` after them."""
    doc = '    """Deactivate a seat at the end of the billing cycle. No refund."""  # r[impl FEAT-001.C1]\n'
    return write(SEATS, f"{signature}\n{doc}{body}")


# --- the seeded changes --------------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    id: str
    kind: str
    edit: Edit
    expect: dict[str, str] = field(default_factory=dict)  # labels other than none


CLAIM_BROKEN = {"FEAT-001.C1": "broken", "REQ-F-002": "broken"}

CHANGES = [
    # refactors: the code changes, the linked test still passes -> verified with the note `changed`
    Change("refactor-local-variable", "refactor",
           seats_body("    pending = account.pending_removals\n    pending.append(seat)\n"), {"FEAT-001.C1": "changed"}),
    Change("refactor-extend", "refactor",
           seats_body("    account.pending_removals.extend([seat])\n"), {"FEAT-001.C1": "changed"}),
    Change("refactor-guard-duplicates", "refactor",
           seats_body("    if seat not in account.pending_removals:\n        account.pending_removals.append(seat)\n"),
           {"FEAT-001.C1": "changed"}),
    Change("refactor-extract-helper", "refactor",
           write(SEATS, (
               "def _queue(account, seat):\n    account.pending_removals.append(seat)\n\n\n"
               "def remove_seat(account, seat):\n"
               '    """Deactivate a seat at the end of the billing cycle. No refund."""  # r[impl FEAT-001.C1]\n'
               "    _queue(account, seat)\n"
           )), {"FEAT-001.C1": "changed"}),
    Change("refactor-rename-parameter", "refactor",
           seats_body("    account.pending_removals.append(removed)\n", "def remove_seat(account, removed):"),
           {"FEAT-001.C1": "changed"}),
    # format-only: whitespace, line breaks, quote style -> no fingerprint moves, nothing to report
    Change("format-spacing", "format",
           seats_body("    account.pending_removals.append( seat )\n", "def remove_seat( account , seat ):")),
    Change("format-line-wrap", "format",
           seats_body("    account.pending_removals.append(\n        seat\n    )\n", "def remove_seat(\n    account, seat\n):")),
    Change("format-quotes-and-blank-lines", "format",
           write(SEATS, (
               "\n\ndef remove_seat(account, seat):\n"
               "    '''Deactivate a seat at the end of the billing cycle. No refund.'''  # r[impl FEAT-001.C1]\n\n"
               "    account.pending_removals.append(seat)\n\n"
           ))),
    # renames: the code anchor no longer resolves -> broken
    Change("rename-function", "rename", replace(SEATS, "def remove_seat(", "def deactivate_seat("), CLAIM_BROKEN),
    Change("rename-file", "rename", move(SEATS, "services/billing/seat_removal.py"), CLAIM_BROKEN),
    Change("rename-into-class", "rename",
           write(SEATS, (
               "class Seats:\n"
               "    def remove_seat(self, account, seat):\n"
               '        """Deactivate a seat at the end of the billing cycle. No refund."""  # r[impl FEAT-001.C1]\n'
               "        account.pending_removals.append(seat)\n"
           )), CLAIM_BROKEN),
    # deleted tests: the linked test is gone -> broken, never unverified
    Change("delete-test-file", "deleted_test", delete(SEAT_TEST), CLAIM_BROKEN),
    Change("delete-test-function", "deleted_test",
           write(SEAT_TEST, "def test_seat_count():\n    ...\n"), CLAIM_BROKEN),
    Change("rename-test-function", "deleted_test",
           replace(SEAT_TEST, "def test_no_refund_on_removal(", "def test_no_refund("), CLAIM_BROKEN),
    # segment rewordings: the cited segment changes, every quote is still found -> stale
    Change("reword-seg-03-prefix", "segment_reword",
           reword(SEG_03, f"Removal takes effect at the next renewal. {SEG_03}"),
           {"REQ-F-001": "stale", "REQ-F-002": "stale"}),
    Change("reword-seg-03-suffix", "segment_reword",
           reword(SEG_03, f"{SEG_03} A removed seat can be added back at any time."),
           {"REQ-F-001": "stale", "REQ-F-002": "stale"}),
    Change("reword-seg-02-suffix", "segment_reword",
           reword(SEG_02, f"{SEG_02} The charge appears on the next invoice."), {"REQ-F-003": "stale"}),
    # quote removals: a cited quote is no longer in its segment -> broken (others citing the segment: stale)
    Change("remove-quote-no-refund", "quote_removal",
           reword("No refund is given.", "A pro-rata refund is given."), {"REQ-F-001": "stale", "REQ-F-002": "broken"}),
    Change("remove-quote-pro-rata", "quote_removal",
           reword(SEG_02, "Adding a seat mid-cycle is charged in full."), {"REQ-F-003": "broken"}),
    # an unrelated file: nothing is affected
    Change("unrelated-readme", "unrelated", write("README.md", "# acme billing\n\nInternal notes.\n")),
]


# --- running -------------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | GIT_ENV
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, env=env, check=True, capture_output=True)


def _fixture_copy(dest: Path, fixture: Path) -> Path:
    """kb_valid committed on main, with the pytest preset on MOD-billing and `.kb/` ignored."""
    shutil.copytree(fixture, dest)
    cfg = dest / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("  risk: high\n", "  risk: high\n  tests:\n    preset: pytest\n", 1))
    (dest / ".gitignore").write_text(".kb/\n")
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "kb_valid")
    return dest


def label_of(verdict) -> str:
    if verdict.status in ("broken", "stale"):
        return verdict.status
    return "changed" if "changed" in verdict.notes else "none"


def labelled_ids(fixture: Path) -> list[str]:
    """Every claim and requirement of the fixture: the ids each change is labelled over."""
    repo = KbRepo.load(fixture)
    return sorted({c.id for _, c in repo.claims()} | {rid for rid in repo.records if rid.startswith("REQ-")})


def run_change(change: Change, scratch: Path, fixture: Path, ids: list[str]) -> dict:
    root = _fixture_copy(scratch / change.id, fixture)
    _git(root, "checkout", "-q", "-b", "change")
    change.edit(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", change.id)
    report = run_verify(root, base="main", all_=False, forge=None)
    found = {v.id: label_of(v) for v in report.verdicts if v.kind in ("claim", "requirement")}
    every = sorted(set(ids) | set(found))
    expected = {i: change.expect.get(i, "none") for i in every}
    predicted = {i: found.get(i, "none") for i in every}
    return {
        "id": change.id, "kind": change.kind, "checked": sorted(found),
        "expected": expected, "predicted": predicted, "correct": expected == predicted,
    }


def score(per_change: list[dict]) -> tuple[float, float]:
    tp = fp = fn = 0
    for c in per_change:
        for i, want in c["expected"].items():
            got = c["predicted"][i]
            tp += got != "none" and got == want
            fp += got != "none" and got != want
            fn += want != "none" and got != want
    return (tp / (tp + fp) if tp + fp else 1.0), (tp / (tp + fn) if tp + fn else 1.0)


def run_staleness(changes: list[Change] = CHANGES, fixture: Path = FIXTURE) -> dict:
    for c in changes:
        if not set(c.expect.values()) <= set(LABELS) - {"none"}:
            raise ValueError(f"{c.id}: labels must be among {LABELS}")
    ids = labelled_ids(fixture)
    with tempfile.TemporaryDirectory(prefix="kb-staleness-") as tmp:
        per_change = [run_change(c, Path(tmp), fixture, ids) for c in changes]
    precision, recall = score(per_change)
    return {"changes": len(per_change), "precision": round(precision, 4), "recall": round(recall, 4), "per_change": per_change}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=RESULTS, help="Where to write the results JSON.")
    args = parser.parse_args(argv)
    result = run_staleness()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    wrong = [c["id"] for c in result["per_change"] if not c["correct"]]
    print(f"{result['changes']} changes: precision {result['precision']:.2f}, recall {result['recall']:.2f} -> {args.out}")
    if wrong:
        print(f"mislabelled: {', '.join(wrong)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
