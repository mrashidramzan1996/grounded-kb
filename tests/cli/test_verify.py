import json
from pathlib import Path

from helpers import CLAIM_TEST, commit, fail_claim_test, forge_file, git, head, invoke, set_mode, verdicts

from kb.ports.forge import COMMENT_MARKER
from kb.verify.report import COMMENT_PATH, STATUS_PATH


def test_verify_all_green_exit_0_and_writes_status_json(verify_repo):
    r = invoke(verify_repo, "verify", "--all")
    assert r.exit_code == 0, r.output
    data = json.loads((verify_repo / STATUS_PATH).read_text())
    assert data["head"] == head(verify_repo) and data["base"] is None
    by_id = {v["id"]: v for v in data["verdicts"]}
    assert by_id["FEAT-001.C1"]["status"] == "verified" and by_id["FEAT-001.C1"]["level"] == "E2"
    assert by_id["REQ-F-002"]["status"] == "verified"
    assert data["blocking"] == []
    assert (verify_repo / COMMENT_PATH).read_text().startswith(COMMENT_MARKER)
    assert (verify_repo / ".kb/cache/index.json").is_file()
    assert "0 broken" in r.stderr and "0 blocking (mode: warn-only)" in r.stderr


def test_broken_claim_exit_1_in_enforce_0_in_warn_only(verify_repo):
    git(verify_repo, "checkout", "-q", "-b", "feature")
    fail_claim_test(verify_repo)
    r = invoke(verify_repo, "verify")
    assert r.exit_code == 0, r.output
    assert r.stdout.splitlines()[0].startswith("broken FEAT-001.C1 E1: ")
    assert "error claim.broken" not in r.stdout  # the verdict line already says it
    assert "2 broken, 0 stale, 0 unverified, 0 verified, 0 built, 0 not_built; 2 blocking (mode: warn-only)" in r.stderr
    assert invoke(verify_repo, "verify", "--strict").exit_code == 1
    set_mode(verify_repo, "enforce")  # the mode is the merge-base's: turning it on in the change does not count...
    r = invoke(verify_repo, "verify")
    assert r.exit_code == 0, r.output
    assert "blocking (mode: warn-only)" in r.stderr
    git(verify_repo, "checkout", "-q", "main")  # ...committed on the base, it does
    git(verify_repo, "commit", "-q", "-m", "enforce", "kb.config.yaml")
    git(verify_repo, "checkout", "-q", "-B", "feature", "main")
    r = invoke(verify_repo, "verify")
    assert r.exit_code == 1, r.output
    assert "blocking (mode: enforce)" in r.stderr


def test_base_computes_changes_and_affected_only(verify_repo):
    git(verify_repo, "checkout", "-q", "-b", "feature")
    fail_claim_test(verify_repo)
    commit(verify_repo)
    r = invoke(verify_repo, "verify", "--base", "main", "--format", "json")
    data = json.loads(r.stdout)
    assert data["base"] == git(verify_repo, "rev-parse", "main").strip() and data["scope"] == "affected"
    assert "INV-001" not in verdicts(r.stdout)  # not affected by the change
    assert verdicts(r.stdout)["FEAT-001.C1"]["status"] == "broken"
    blocking = data["blocking"]
    assert {"code": "claim.broken", "record": "FEAT-001.C1"} in blocking
    issue = next(i for i in data["issues"] if i["code"] == "claim.broken")
    assert issue["path"] == "kb/features/FEAT-001.md" and issue["message"].startswith("broken: ")


def test_json_stdout_only_messages_stderr(verify_repo):
    r = invoke(verify_repo, "verify", "--all", "--format", "json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert set(data) == {"base", "head", "mode", "scope", "verdicts", "issues", "blocking", "summary"}
    assert data["scope"] == "all"
    assert set(data["summary"]) == {"broken", "stale", "unverified", "verified", "built", "not_built", "errors", "warnings"}
    assert set(data["verdicts"][0]) == {"id", "kind", "status", "level", "notes", "reasons", "tests"}
    assert r.stdout == (verify_repo / STATUS_PATH).read_text()
    assert r.stdout == json.dumps(data, sort_keys=True, indent=2) + "\n"
    assert str(verify_repo) not in r.stdout and str(verify_repo.resolve()) not in r.stdout
    assert "blocking (mode:" in r.stderr and "blocking (mode:" not in r.stdout


def test_output_is_byte_identical_across_runs(verify_repo):
    first = invoke(verify_repo, "verify", "--all", "--format", "json").stdout
    md = (verify_repo / COMMENT_PATH).read_text()
    assert invoke(verify_repo, "verify", "--all", "--format", "json").stdout == first
    assert (verify_repo / COMMENT_PATH).read_text() == md


def test_markdown_starts_with_marker_and_caps_rows(verify_repo):
    cfg = verify_repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text() + "review:\n  max_items_per_pr: 1\n")
    fail_claim_test(verify_repo)
    r = invoke(verify_repo, "verify", "--all", "--format", "markdown")
    assert r.exit_code == 0, r.output
    md = r.stdout
    assert md.startswith(COMMENT_MARKER + "\n")
    assert md == (verify_repo / COMMENT_PATH).read_text()
    assert "**kb verify**: 2 broken · 0 stale · 1 unverified · 0 verified · 0 built · 2 not_built (mode: warn-only)" in md
    sections = [ln for ln in md.splitlines() if ln.startswith("### ")]
    assert sections == ["### Blocking", "### Claims", "### Approvals", "### Other issues"]
    claims = md.split("### Claims", 1)[1].split("### Approvals", 1)[0]
    rows = [ln for ln in claims.splitlines() if ln.startswith("| ") and not ln.startswith("| ID")]
    assert len(rows) == 1 and rows[0].startswith("| FEAT-001.C1 | broken | E1 |")  # broken first
    assert "… and 4 more" in claims


def test_comment_upserted_via_local_forge(verify_repo, tmp_path):
    git(verify_repo, "checkout", "-q", "-b", "feature")
    (verify_repo / "services/billing/seats.py").write_text(
        (verify_repo / "services/billing/seats.py").read_text() + "\n\ndef add_seat(account):\n    return 1\n"
    )
    commit(verify_repo)
    path = forge_file(tmp_path, verify_repo)
    r = invoke(verify_repo, "verify", "--forge", f"local:{path}", "--comment", "--format", "json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert {"code": "missing_owner_approval", "record": None} in data["blocking"]
    (body,) = json.loads(path.read_text())["comments"]
    assert body.startswith(COMMENT_MARKER) and body == (verify_repo / COMMENT_PATH).read_text()
    approvals = body.split("### Approvals", 1)[1].split("### Other issues", 1)[0]
    assert "@acme/billing" in approvals and "changes services/billing/seats.py" in approvals

    # an approval on the head commit by a team member satisfies the rule; the one comment is replaced
    review = {"login": "bob", "state": "APPROVED", "commit_id": head(verify_repo), "submitted_at": "2026-01-01T00:00:00Z"}
    stored = json.loads(path.read_text())
    path.write_text(json.dumps(stored | {"reviews": [review], "teams": {"@acme/billing": ["bob"]}}))
    r = invoke(verify_repo, "verify", "--forge", f"local:{path}", "--comment", "--format", "json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["blocking"] == []
    (body2,) = json.loads(path.read_text())["comments"]
    assert body2 != body and body2.startswith(COMMENT_MARKER)


def test_missing_owner_approval_blocks_in_enforce(verify_repo, tmp_path):
    set_mode(verify_repo, "enforce")
    commit(verify_repo, "enforce")
    git(verify_repo, "checkout", "-q", "-b", "feature")
    (verify_repo / "services/billing/seats.py").write_text("def remove_seat(account, seat):  # r[impl FEAT-001.C1]\n    return 0\n")
    commit(verify_repo)
    r = invoke(verify_repo, "verify", "--forge", f"local:{forge_file(tmp_path, verify_repo)}")
    assert r.exit_code == 1, r.output
    assert "error missing_owner_approval" in r.stdout


def test_comment_without_pull_request_is_skipped(verify_repo, tmp_path):
    path = forge_file(tmp_path, verify_repo, pr=None)
    r = invoke(verify_repo, "verify", "--all", "--forge", f"local:{path}", "--comment")
    assert r.exit_code == 0, r.output
    assert "no pull request; comment skipped" in r.stderr
    assert json.loads(path.read_text())["comments"] == []
    r = invoke(verify_repo, "verify", "--all", "--comment")  # --forge auto outside Actions: no forge
    assert r.exit_code == 0 and "no pull request; comment skipped" in r.stderr


def test_shallow_clone_exits_2_with_hint(verify_repo):
    git(verify_repo, "checkout", "-q", "--orphan", "unrelated")
    commit(verify_repo, "unrelated root")
    git(verify_repo, "checkout", "-q", "main")
    r = invoke(verify_repo, "verify", "--base", "unrelated")
    assert r.exit_code == 2
    assert "fetch-depth: 0" in r.stderr and "Traceback" not in r.output


def test_shallow_clone_default_path_exits_2_with_hint(verify_repo, tmp_path):
    git(verify_repo, "checkout", "-q", "-b", "feature")
    for n in (1, 2):  # two commits: depth 1 cuts the branch off from main's tip
        (verify_repo / "services/billing/notes.py").write_text(f"X = {n}\n")
        commit(verify_repo, f"extra commit {n}")
    git(verify_repo, "checkout", "-q", "main")  # the remote's HEAD names its default branch
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", "--no-single-branch", "--branch", "feature",
        f"file://{verify_repo}", str(clone))
    assert git(clone, "rev-parse", "--verify", "origin/main").strip()  # the default branch resolves...
    r = invoke(clone, "verify")  # ...but its merge-base with HEAD is not in the shallow history
    assert r.exit_code == 2, r.output
    assert "fetch-depth: 0" in r.stderr and "Traceback" not in r.output


def test_no_default_branch_acts_as_all(verify_repo):
    git(verify_repo, "branch", "-m", "main", "trunk")
    r = invoke(verify_repo, "verify", "--format", "json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["base"] is None and data["scope"] == "all" and "INV-001" in verdicts(r.stdout)


def test_unknown_forge_value_exits_2(verify_repo):
    r = invoke(verify_repo, "verify", "--forge", "gitlab")
    assert r.exit_code == 2 and "unknown forge" in r.stderr and "Traceback" not in r.output


def test_unknown_format_exits_2(verify_repo):
    assert invoke(verify_repo, "verify", "--format", "xml").exit_code == 2


def test_test_command_that_cannot_start_exits_2(verify_repo):
    cfg = verify_repo / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("preset: pytest", "run: kb-no-such-runner {tests} --junitxml={junit}"))
    r = invoke(verify_repo, "verify", "--all")
    assert r.exit_code == 2 and "cannot run test command" in r.stderr


def test_comment_post_failure_is_a_warning(verify_repo, tmp_path, monkeypatch):
    from kb.forge import ForgeError
    from kb.forge.local import LocalForge

    class Denied(LocalForge):
        def upsert_comment(self, pr, body):
            raise ForgeError("403 Resource not accessible by integration")

    path = forge_file(tmp_path, verify_repo)
    monkeypatch.setattr("kb.cli.forge_from_option", lambda value, root: Denied(path))
    r = invoke(verify_repo, "verify", "--forge", "auto", "--comment")
    assert r.exit_code == 0, r.output
    assert "kb: could not post the PR comment: 403 Resource not accessible by integration" in r.stderr


def test_cache_files_written_for_every_format(verify_repo):
    for fmt in ("text", "json", "markdown"):
        for name in (STATUS_PATH, COMMENT_PATH):
            (verify_repo / name).unlink(missing_ok=True)
        assert invoke(verify_repo, "verify", "--all", "--format", fmt).exit_code == 0
        assert (verify_repo / STATUS_PATH).is_file() and (verify_repo / COMMENT_PATH).is_file(), fmt


def test_pr_snapshot_taken_before_tests(verify_repo, tmp_path):
    """A linked test that rewrites the forge data (here: fakes an approval on a made-up head) cannot change the PR
    whose approvals are checked: the PR is read before any test runs."""
    git(verify_repo, "checkout", "-q", "-b", "feature")
    seats = verify_repo / "services/billing/seats.py"
    seats.write_text(seats.read_text() + "\n\ndef add_seat(account):\n    return 1\n")
    path = tmp_path / "forge.json"
    fake = {
        "pr": {"number": 7, "head_sha": "f" * 40, "author": "alice"},
        "reviews": [{"login": "bob", "state": "APPROVED", "commit_id": "f" * 40, "submitted_at": "2026-01-01T00:00:00Z"}],
        "teams": {"@acme/billing": ["bob"]},
        "comments": [],
    }
    test = verify_repo / CLAIM_TEST
    test.write_text(test.read_text().replace(
        "    ...\n", f"    import pathlib\n    pathlib.Path({str(path)!r}).write_text({json.dumps(fake)!r})\n", 1))
    commit(verify_repo)
    assert forge_file(tmp_path, verify_repo) == path
    r = invoke(verify_repo, "verify", "--forge", f"local:{path}", "--format", "json")
    assert r.exit_code == 0, r.output
    assert json.loads(path.read_text())["pr"]["head_sha"] == "f" * 40  # the test did run and rewrite it
    data = json.loads(r.stdout)
    assert verdicts(r.stdout)["FEAT-001.C1"]["status"] == "verified"
    assert {"code": "missing_owner_approval", "record": None} in data["blocking"]
    assert any(head(verify_repo)[:7] in i["message"] for i in data["issues"] if i["code"] == "missing_owner_approval")


def test_codeowners_snapshot_taken_before_tests(verify_repo):
    """A linked test that rewrites CODEOWNERS (and kb.config.yaml) mid-run cannot change who must approve: both are
    read before any test runs."""
    (verify_repo / "CODEOWNERS").write_text("services/** @acme/billing\n")
    commit(verify_repo, "codeowners")
    git(verify_repo, "checkout", "-q", "-b", "feature")
    seats = verify_repo / "services/billing/seats.py"
    seats.write_text(seats.read_text() + "\n\ndef add_seat(account):\n    return 1\n")
    test = verify_repo / CLAIM_TEST
    test.write_text(test.read_text().replace("    ...\n", (
        "    import pathlib\n"
        "    pathlib.Path('CODEOWNERS').write_text('* @mallory\\n')\n"
        "    cfg = pathlib.Path('kb.config.yaml')\n"
        "    cfg.write_text(cfg.read_text().replace('owner: team-billing', 'owner: team-platform'))\n"
    ), 1))
    commit(verify_repo)
    path = forge_file(verify_repo.parent, verify_repo)
    r = invoke(verify_repo, "verify", "--forge", f"local:{path}", "--format", "json")
    assert r.exit_code == 0, r.output
    assert (verify_repo / "CODEOWNERS").read_text() == "* @mallory\n"  # the test did run and rewrite it
    assert verdicts(r.stdout)["FEAT-001.C1"]["status"] == "verified"
    missing = [i["message"] for i in json.loads(r.stdout)["issues"] if i["code"] == "missing_owner_approval"]
    assert missing and all(m.startswith("@acme/billing has not approved") for m in missing)
    assert "@mallory" not in r.stdout and "@acme/platform" not in r.stdout


def test_unreadable_base_policy_exits_2(tmp_path):
    from helpers import make_git_repo

    root = make_git_repo(None, tmp_path, {"kb.config.yaml": "version: [\n"})
    tip = head(root)
    git(root, "checkout", "-q", "-b", "feature")
    (root / "kb.config.yaml").write_text("version: 1\n")
    commit(root, "a config that parses")
    r = invoke(root, "verify", "--base", "main", "--forge", "none")
    assert r.exit_code == 2, r.output
    assert f"kb.config.yaml at {tip}" in r.output


def test_skip_tests_runs_no_tests_and_checks_approvals(verify_repo, tmp_path):
    git(verify_repo, "checkout", "-q", "-b", "feature")
    seats = verify_repo / "services/billing/seats.py"
    seats.write_text(seats.read_text() + "\n\ndef add_seat(account):\n    return 1\n")
    marker = tmp_path / "ran"
    test = verify_repo / CLAIM_TEST
    test.write_text(test.read_text().replace(
        "    ...\n", f"    import pathlib\n    pathlib.Path({str(marker)!r}).write_text('ran')\n", 1))
    commit(verify_repo)
    path = forge_file(tmp_path, verify_repo)
    r = invoke(verify_repo, "verify", "--skip-tests", "--forge", f"local:{path}", "--format", "json")
    assert r.exit_code == 0, r.output
    assert not marker.exists()  # no project test ran
    claim = verdicts(r.stdout)["FEAT-001.C1"]
    assert claim["status"] == "unverified" and "tests not run (--skip-tests)" in claim["reasons"]
    assert {"code": "missing_owner_approval", "record": None} in json.loads(r.stdout)["blocking"]
    assert "broken" not in {v["status"] for v in verdicts(r.stdout).values()}


def test_a_submodule_pointer_update_does_not_break_verify(verify_repo):
    git(verify_repo, "update-index", "--add", "--cacheinfo", f"160000,{git(verify_repo, 'rev-parse', 'HEAD').strip()},vendor/sub")
    git(verify_repo, "commit", "-q", "-m", "add submodule")
    git(verify_repo, "checkout", "-q", "-b", "feature")
    git(verify_repo, "update-index", "--cacheinfo", f"160000,{'1' * 40},vendor/sub")
    git(verify_repo, "commit", "-q", "-m", "bump submodule")
    r = invoke(verify_repo, "verify", "--base", "main", "--forge", "none")
    assert r.exit_code != 2, r.output
