"""Phase 2 acceptance: one exit criterion per test, `kb verify` end to end on a git copy of kb_valid.

Each test starts on `main` with the pytest preset on both modules, makes its change on a `feature` branch and runs
`kb verify --base main` against a `local:` forge whose PR head is the branch tip, approved on that commit by a member of
every team (except where a test leaves an approval out).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import adr_bytes, commit, forge_file, git, head, invoke, set_mode, verdicts, write

SEATS = "services/billing/seats.py"
SPEC = "docs/pricing-spec-v3.md"
TEAMS = {"@acme/billing": ["bob"], "@acme/platform": ["pat"], "@acme/product": ["paula"], "@acme/tech-leads": ["tess"]}
# kb_valid's records of MOD-billing: FEAT-001 is in the module and every requirement names it
BILLING_RECORDS = {"FEAT-001.C1", "REQ-F-001", "REQ-F-002", "REQ-F-003"}


@pytest.fixture
def repo(verify_repo):
    """verify_repo (pytest preset on MOD-billing) with the preset on MOD-sessions too, so INV-001's test runs."""
    cfg = verify_repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("  owner: team-platform\n", "  owner: team-platform\n  tests:\n    preset: pytest\n", 1))
    commit(verify_repo, "test runner for MOD-sessions")
    return verify_repo


def branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "feature")


def approving_forge(tmp_path: Path, repo: Path, approvers=tuple(TEAMS)) -> str:
    """A local forge for PR #7 at HEAD, approved on HEAD by one member of each team in `approvers`."""
    reviews = [
        {"login": TEAMS[t][0], "state": "APPROVED", "commit_id": head(repo), "submitted_at": "2026-11-24T00:00:00Z"}
        for t in approvers
    ]
    return f"local:{forge_file(tmp_path, repo, reviews=reviews, teams=TEAMS)}"


def verify(repo: Path, tmp_path: Path, **forge):
    """`kb verify --base main --format json` with an approving forge: (result, parsed JSON)."""
    r = invoke(repo, "verify", "--base", "main", "--forge", approving_forge(tmp_path, repo, **forge), "--format", "json")
    assert r.exit_code in (0, 1), r.output
    return r, json.loads(r.stdout)


def blocking_codes(data: dict) -> set[str]:
    return {b["code"] for b in data["blocking"]}


def test_appendix_c_refactor_verified_with_changed_note(repo, tmp_path):
    branch(repo)
    write(repo, SEATS, (
        "def remove_seat(account, seat):\n"
        '    """Deactivate a seat at the end of the billing cycle. No refund."""  # r[impl FEAT-001.C1]\n'
        "    pending = account.pending_removals\n"
        "    pending.append(seat)\n"
    ))
    commit(repo, "refactor remove_seat")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 0, r.output
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert (claim["status"], claim["level"], claim["notes"]) == ("verified", "E2", ["changed"])
    assert claim["tests"] == ["tests/test_seats.py::test_no_refund_on_removal"]
    assert verdicts(r.stdout)["REQ-F-002"]["status"] == "verified"
    assert data["blocking"] == []


def test_rename_breaks_claim_and_fails_pr(repo, tmp_path):
    set_mode(repo, "enforce")
    commit(repo, "enforce")
    branch(repo)
    path = repo / SEATS
    path.write_text(path.read_text().replace("def remove_seat(", "def deactivate_seat("))
    commit(repo, "rename remove_seat")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 1, r.output
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert claim["status"] == "broken"
    assert claim["reasons"] == [f"anchor {SEATS}#remove_seat no longer resolves"]
    assert {"code": "claim.broken", "record": "FEAT-001.C1"} in data["blocking"]
    assert verdicts(r.stdout)["REQ-F-002"]["status"] == "broken"
    assert "blocking (mode: enforce)" in r.stderr


def test_ruff_format_changes_no_fingerprint_end_to_end(repo, tmp_path):
    write(repo, SEATS, (
        "def remove_seat( account,seat ):\n"
        "    '''Deactivate a seat at the end of the billing cycle. No refund.'''  # r[impl FEAT-001.C1]\n"
        "    account.pending_removals.append( seat\n"
        "    )\n"
        "def add_seat(account,seat): account.seats.append(seat)\n"
    ))
    commit(repo, "unformatted billing code")
    branch(repo)
    before = (repo / SEATS).read_text()
    subprocess.run([sys.executable, "-m", "ruff", "format", "--no-cache", "services/"], cwd=repo, check=True, capture_output=True)
    assert (repo / SEATS).read_text() != before  # ruff did reformat it
    commit(repo, "ruff format")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 0, r.output
    assert "FEAT-001.C1" in verdicts(r.stdout)  # the change did reach the claim...
    assert [v["id"] for v in data["verdicts"] if "changed" in v["notes"]] == []  # ...but no fingerprint moved
    assert verdicts(r.stdout)["FEAT-001.C1"]["status"] == "verified"


def test_deleting_linked_test_breaks_claim(repo, tmp_path):
    branch(repo)
    (repo / "tests/test_seats.py").unlink()
    commit(repo, "delete the seat test")
    r, data = verify(repo, tmp_path)
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert (claim["status"], claim["level"]) == ("broken", "E1")  # never `unverified`, "no linked test"
    assert claim["reasons"] == ["linked test tests/test_seats.py::test_no_refund_on_removal was deleted"]
    assert {"code": "claim.broken", "record": "FEAT-001.C1"} in data["blocking"]


def test_flaky_test_quarantined(repo, tmp_path):
    set_mode(repo, "enforce")
    commit(repo, "enforce")
    branch(repo)
    ran = tmp_path / "ran-once"
    write(repo, "tests/test_seats.py", (
        "from pathlib import Path\n\n\n"
        "# r[verify FEAT-001.C1]\n"
        "def test_no_refund_on_removal():\n"
        f"    ran = Path({str(ran)!r})\n"
        "    first = not ran.exists()\n"
        "    ran.touch()\n"
        "    assert not first, 'fails on the first run only'\n"
    ))
    commit(repo, "a flaky test")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 0, r.output  # quarantined: a warning, never a block
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert (claim["status"], claim["level"], claim["notes"]) == ("unverified", "E1", ["flaky"])
    assert claim["reasons"] == ["tests/test_seats.py::test_no_refund_on_removal is flaky; quarantined"]
    assert "flaky" in verdicts(r.stdout)["REQ-F-002"]["notes"]
    assert data["blocking"] == []


def test_lockfile_bump_rechecks_only_importing_module(repo, tmp_path):
    lock = 'version = 1\n\n[[package]]\nname = "stripe"\nversion = "{}"\n\n[[package]]\nname = "redis"\nversion = "5.0.0"\n'
    write(repo, "uv.lock", lock.format("1.0.0"))
    write(repo, "services/billing/payments.py", "import stripe\n\n\ndef charge(cents):\n    return stripe.charge(cents)\n")
    cfg = repo / "kb.config.yaml"  # give MOD-sessions code that imports another locked package
    cfg.write_text(cfg.read_text().replace("- id: MOD-sessions\n", "- id: MOD-sessions\n  paths:\n  - services/sessions/**\n", 1))
    write(repo, "services/sessions/store.py", "import redis\n\n\ndef client():\n    return redis.Redis()\n")
    commit(repo, "lockfile and payments")
    branch(repo)
    write(repo, "uv.lock", lock.format("2.0.0"))
    commit(repo, "bump stripe")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 0, r.output
    hit = set(verdicts(r.stdout))
    assert "FEAT-001.C1" in hit and hit <= BILLING_RECORDS
    assert "INV-001" not in hit  # MOD-sessions imports redis, not stripe


def test_appendix_d_segment_wording_change_makes_requirements_stale_and_needs_intent_approvers(repo, tmp_path):
    """kb_valid's requirements cite seg-03 ("3.2 Removing seats"), the fixture's counterpart of seg-08."""
    branch(repo)
    old = "A removed seat stays active until the end of the billing cycle. No refund is given."
    spec = repo / SPEC
    assert old in spec.read_text()
    spec.write_text(spec.read_text().replace(old, f"Removal takes effect at the next renewal. {old}"))
    assert invoke(repo, "source", "sync").exit_code == 0
    stored = (repo / "kb/sources/SRC-pricing-spec-v3/segments.jsonl").read_text()
    assert '"id": "seg-03", "heading": "Pricing Spec v3 > 3.2 Removing seats"' in stored
    commit(repo, "reword the seat removal section")

    no_intent = tuple(t for t in TEAMS if t != "@acme/product")
    r, data = verify(repo, tmp_path, approvers=no_intent)
    by_id = verdicts(r.stdout)
    for rid in ("REQ-F-001", "REQ-F-002"):  # both quotes are still found in the reworded segment
        assert by_id[rid]["status"] == "stale", by_id[rid]
        assert by_id[rid]["reasons"][0] == (
            "cited segment SRC-pricing-spec-v3#seg-03 changed; needs intent approvers' review"
        )
    assert "REQ-F-003" not in by_id  # cites seg-02, which did not change
    missing = [i for i in data["issues"] if i["code"] == "missing_owner_approval"]
    assert len(missing) == 1 and missing[0]["message"].startswith("@acme/product has not approved")
    assert "requirement REQ-F-001 is stale" in missing[0]["message"]
    assert {"code": "missing_owner_approval", "record": None} in data["blocking"]

    _, data = verify(repo, tmp_path)  # the intent approvers approve the head commit
    assert not [i for i in data["issues"] if i["code"] == "missing_owner_approval"]


def test_stale_context_before_rebase_fails_after_rebase_passes(repo, tmp_path):
    set_mode(repo, "enforce")
    commit(repo, "enforce")
    branch(repo)
    path = repo / SEATS
    path.write_text(path.read_text() + "\n\ndef add_seat(account, seat):\n    account.seats.append(seat)\n")
    commit(repo, "add_seat")
    git(repo, "checkout", "-q", "main")  # meanwhile on main: a decision now governs MOD-billing
    (repo / "kb/decisions/ADR-0004.md").write_bytes(adr_bytes("ADR-0004", governs=["MOD-billing"]))
    commit(repo, "ADR-0004")
    git(repo, "checkout", "-q", "feature")

    r, data = verify(repo, tmp_path)
    assert r.exit_code == 1, r.output
    assert {"code": "stale_context", "record": "ADR-0004"} in data["blocking"]
    assert "rebase on main" in next(i["message"] for i in data["issues"] if i["code"] == "stale_context")

    git(repo, "rebase", "-q", "main")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 0, r.output
    assert "stale_context" not in {i["code"] for i in data["issues"]}
    assert data["blocking"] == []


def test_invariant_failing_test_fails_pr(repo, tmp_path):
    set_mode(repo, "enforce")
    commit(repo, "enforce")
    branch(repo)
    path = repo / "tests/test_sessions.py"
    path.write_text(path.read_text().replace("    ...\n", "    assert False\n", 1))
    commit(repo, "break the session test")
    r, data = verify(repo, tmp_path)
    assert r.exit_code == 1, r.output
    inv = verdicts(r.stdout)["INV-001"]
    assert inv["status"] == "broken"
    assert inv["reasons"] == ["tests/test_sessions.py::test_session_expires_after_30_minutes failed"]
    assert blocking_codes(data) == {"invariant.broken"}


def test_pr_cannot_loosen_its_own_policy(repo, tmp_path):
    """The approvers, CODEOWNERS and mode are the merge-base's: a PR that empties `intent`, drops the CODEOWNERS lines
    and turns enforcement off still needs the intent approvers for its requirement change, and the decision approvers
    for the policy change itself."""
    write(repo, ".github/CODEOWNERS", "/kb/requirements/ @acme/product\n/kb.config.yaml @acme/tech-leads\n")
    set_mode(repo, "enforce")
    commit(repo, "CODEOWNERS block, enforce")
    base = head(repo)
    branch(repo)
    cfg = repo / "kb.config.yaml"
    text = cfg.read_text()
    loosened = text.replace("    intent:\n    - product-owners\n", "    intent: []\n").replace(
        "  mode: enforce\n", "  mode: warn-only\n  fail_on: []\n"
    )
    assert loosened.count("intent: []") == 1 and "mode: warn-only" in loosened
    cfg.write_text(loosened)
    write(repo, ".github/CODEOWNERS", "")
    req = repo / "kb/requirements/REQ-F-001.yaml"
    req.write_text(req.read_text().replace("statement: A removed seat", "statement: A deleted seat", 1))
    commit(repo, "loosen the policy and change a requirement")

    unapproved = f"local:{forge_file(tmp_path, repo, teams=TEAMS)}"
    r = invoke(repo, "verify", "--base", "main", "--forge", unapproved, "--format", "markdown")
    assert r.exit_code == 1, r.output
    approvals = r.stdout.split("### Approvals\n", 1)[1].split("### Other issues", 1)[0]
    assert f"Policy from base main at {base[:7]}." in approvals
    product = next(ln for ln in approvals.splitlines() if ln.startswith("- `@acme/product`"))
    assert "requirement REQ-F-001 changed" in product
    leads = next(ln for ln in approvals.splitlines() if ln.startswith("- `@acme/tech-leads`"))
    assert "kb.config.yaml changed" in leads and ".github/CODEOWNERS changed" in leads
    assert "missing_owner_approval" in approvals
    assert "blocking (mode: enforce)" in r.stderr

    both = approving_forge(tmp_path, repo, approvers=("@acme/product", "@acme/tech-leads"))
    r = invoke(repo, "verify", "--base", "main", "--forge", both, "--format", "markdown")
    assert r.exit_code == 0, r.output
    assert "All required owners approved." in r.stdout
