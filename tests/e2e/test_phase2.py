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


def _loosened_pr(repo: Path) -> None:
    """On `feature`: the PR turns enforcement off, empties `intent` and edits REQ-F-001 (main enforces)."""
    set_mode(repo, "enforce")
    commit(repo, "enforce")
    branch(repo)
    cfg = repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("    intent:\n    - product-owners\n", "    intent: []\n").replace(
        "  mode: enforce\n", "  mode: warn-only\n  fail_on: []\n"
    ))
    req = repo / "kb/requirements/REQ-F-001.yaml"
    req.write_text(req.read_text().replace("statement: A removed seat", "statement: A deleted seat", 1))
    commit(repo, "loosen the policy and change a requirement")


def test_tag_named_like_the_base_branch_is_refused(repo, tmp_path):
    """git resolves refs/tags/<name> before refs/remotes/<name>: a tag the PR author pushes (actions/checkout with
    fetch-depth 0 fetches tags) named like the base branch and pointing at the PR head would make the merge-base HEAD
    and the policy the PR's own. A shadowing tag is exit 2; a full ref names the branch unambiguously."""
    _loosened_pr(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")  # as a CI checkout has it
    unapproved = f"local:{forge_file(tmp_path, repo, teams=TEAMS)}"

    git(repo, "tag", "origin/main", "HEAD")
    r = invoke(repo, "verify", "--base", "origin/main", "--forge", unapproved)
    assert r.exit_code == 2, r.output
    assert "a tag named 'origin/main' shadows the branch; delete it or pass a full ref" in r.stderr
    git(repo, "tag", "-d", "origin/main")

    git(repo, "tag", "main", "HEAD")
    for args in (["--base", "main"], [], ["--all"]):  # given, the default branch, and --all's policy branch
        r = invoke(repo, "verify", *args, "--forge", unapproved)
        assert r.exit_code == 2, (args, r.output)
        assert "a tag named 'main' shadows the branch" in r.stderr, args
    pr_base = forge_file(tmp_path, repo, teams=TEAMS, pr={"number": 7, "head_sha": head(repo), "author": "alice",
                                                          "base": "main"})
    r = invoke(repo, "verify", "--all", "--forge", f"local:{pr_base}")  # the PR's own base branch, by name
    assert r.exit_code == 2 and "a tag named 'main' shadows the branch" in r.stderr, r.output

    for ref in ("refs/heads/main", "refs/remotes/origin/main"):  # a full ref is not ambiguous: the base's policy
        r = invoke(repo, "verify", "--base", ref, "--forge", unapproved, "--format", "json")
        assert r.exit_code == 1, (ref, r.output)
        data = json.loads(r.stdout)
        assert "missing_owner_approval" in blocking_codes(data)
        assert any("requirement REQ-F-001 changed" in i["message"] for i in data["issues"])


def _approvals(markdown: str) -> dict[str, str]:
    """{owner handle: its reasons} from the report's Approvals section."""
    section = markdown.split("### Approvals\n", 1)[1].split("\n### ", 1)[0]
    return {ln.split("`")[1]: ln.split("`: ", 1)[1] for ln in section.splitlines() if ln.startswith("- `")}


def test_renaming_codeowners_away_needs_decision_approval(repo, tmp_path):
    """git diff detects renames and lists only the new path: `git mv .github/CODEOWNERS elsewhere` must still count as
    a CODEOWNERS change, and a file moved out of an owned directory still needs its old owners."""
    write(repo, ".github/CODEOWNERS", "/services/billing/ @acme/billing\n/kb/requirements/ @acme/product\n")
    write(repo, "services/billing/notes.py", "X = 1\n")
    set_mode(repo, "enforce")
    commit(repo, "CODEOWNERS, enforce")
    branch(repo)
    (repo / "notes").mkdir()
    git(repo, "mv", ".github/CODEOWNERS", "notes/old-owners.txt")
    git(repo, "mv", "services/billing/notes.py", "notes/notes.py")
    commit(repo, "move CODEOWNERS and a billing file away")

    unapproved = f"local:{forge_file(tmp_path, repo, teams=TEAMS)}"
    r = invoke(repo, "verify", "--base", "main", "--forge", unapproved, "--format", "markdown")
    assert r.exit_code == 1, r.output
    required = _approvals(r.stdout)
    assert ".github/CODEOWNERS changed" in required["@acme/tech-leads"]
    assert "changes services/billing/notes.py" in required["@acme/billing"]
    assert "missing_owner_approval" in r.stdout


def test_tag_named_like_a_full_branch_ref_is_refused(repo, tmp_path):
    """A CI checkout has refs/remotes/origin/main and no local main. `git rev-parse refs/heads/main` then falls through
    to refs/tags/refs/heads/main, a tag the PR author can push: branch refs are looked up by exact full name, and a
    tag named like any candidate ref is exit 2. Never the tag's commit."""
    _loosened_pr(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    tip = git(repo, "rev-parse", "refs/remotes/origin/main").strip()
    git(repo, "branch", "-q", "-D", "main")
    unapproved = f"local:{forge_file(tmp_path, repo, teams=TEAMS)}"

    for tag in ("refs/heads/main", "refs/remotes/origin/main"):
        git(repo, "tag", tag, "HEAD")
        for base in ("main", tag):
            r = invoke(repo, "verify", "--base", base, "--forge", unapproved)
            assert r.exit_code == 2, (tag, base, r.output)
            assert f"a tag named '{tag}' shadows the branch" in r.stderr, (tag, base)
        git(repo, "tag", "-d", tag)

    git(repo, "tag", "refs/heads/main", "HEAD")  # a tag that names no candidate of origin/main: the real tip decides
    r = invoke(repo, "verify", "--base", "origin/main", "--forge", unapproved, "--format", "markdown")
    assert r.exit_code == 1, r.output
    assert f"Policy from base origin/main at {tip[:7]}." in r.stdout
    assert "requirement REQ-F-001 changed" in _approvals(r.stdout)["@acme/product"]


def test_local_branch_shadowing_the_remote_base_is_refused(repo, tmp_path):
    """A short name that more than one branch answers to (a local main and origin/main, or a local branch named
    origin/main) at different commits is ambiguous: exit 2, naming both refs. At the same commit it is not."""
    _loosened_pr(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    unapproved = f"local:{forge_file(tmp_path, repo, teams=TEAMS)}"
    r = invoke(repo, "verify", "--base", "main", "--forge", unapproved)
    assert r.exit_code == 1, r.output  # local main and origin/main agree

    git(repo, "branch", "-f", "main", "HEAD")  # local main moved to the PR head
    r = invoke(repo, "verify", "--base", "main", "--forge", unapproved)
    assert r.exit_code == 2, r.output
    assert "refs/heads/main" in r.stderr and "refs/remotes/origin/main" in r.stderr and "ambiguous" in r.stderr
    git(repo, "branch", "-f", "main", "refs/remotes/origin/main")

    git(repo, "branch", "origin/main", "HEAD")  # refs/heads/origin/main
    r = invoke(repo, "verify", "--base", "origin/main", "--forge", unapproved)
    assert r.exit_code == 2, r.output
    assert "refs/heads/origin/main" in r.stderr and "refs/remotes/origin/main" in r.stderr
    r = invoke(repo, "verify", "--base", "refs/remotes/origin/main", "--forge", unapproved)
    assert r.exit_code == 1, r.output  # the full ref is not ambiguous


def verify_skipping_tests(repo: Path, tmp_path: Path, **forge):
    r = invoke(repo, "verify", "--base", "main", "--skip-tests", "--forge", approving_forge(tmp_path, repo, **forge), "--format", "json")
    assert r.exit_code in (0, 1), r.output
    return r, json.loads(r.stdout)


def test_skip_tests_still_breaks_a_claim_whose_linked_test_was_deleted(repo, tmp_path):
    branch(repo)
    (repo / "tests/test_seats.py").unlink()
    commit(repo, "delete the seat test")
    r, data = verify_skipping_tests(repo, tmp_path)
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert claim["status"] == "broken"  # needs no test run
    assert claim["reasons"] == ["linked test tests/test_seats.py::test_no_refund_on_removal was deleted"]
    assert {"code": "claim.broken", "record": "FEAT-001.C1"} in data["blocking"]


def test_skip_tests_still_marks_a_changed_cited_segment_stale_and_needs_intent_approvers(repo, tmp_path):
    branch(repo)
    old = "A removed seat stays active until the end of the billing cycle. No refund is given."
    spec = repo / SPEC
    spec.write_text(spec.read_text().replace(old, f"Removal takes effect at the next renewal. {old}"))
    assert invoke(repo, "source", "sync").exit_code == 0
    commit(repo, "reword the seat removal section")
    no_intent = tuple(t for t in TEAMS if t != "@acme/product")
    r, data = verify_skipping_tests(repo, tmp_path, approvers=no_intent)
    assert verdicts(r.stdout)["REQ-F-001"]["status"] == "stale"
    (missing,) = [i for i in data["issues"] if i["code"] == "missing_owner_approval"]
    assert missing["message"].startswith("@acme/product has not approved")
