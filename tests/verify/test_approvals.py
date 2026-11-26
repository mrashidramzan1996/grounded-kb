from pathlib import Path

from kb.config.schema import KbConfig
from kb.ports.forge import PullRequest, Review
from kb.records.repo import KbRepo
from kb.verify.approvals import owner_approval, required_owners
from kb.verify.changes import ChangeSet
from kb.verify.engine import Verdict

HEAD = "c" * 40
PR = PullRequest(1, HEAD, "alice")


class FakeForge:
    name = "fake"

    def __init__(self, reviews=(), teams=None):
        self._reviews, self._teams = list(reviews), teams or {}

    def pull_request(self):
        return PR

    def reviews(self, pr):
        return self._reviews

    def team_members(self, handle):
        return self._teams.get(handle)

    def upsert_comment(self, pr, body):
        pass


def cfg(**extra):
    base = {
        "version": 1,
        "modules": [{"id": "MOD-a", "paths": ["src/a/**"], "owner": "@alice-owner"}, {"id": "MOD-b", "paths": ["src/b/**"], "owner": "@bob"}],
        "policy": {"approvers": {"intent": ["@pm"], "decisions": ["@arch"]}},
    }
    return KbConfig.model_validate(base | extra)


def review(login, state="APPROVED", commit=HEAD, at="2026-01-01T00:00:00Z"):
    return Review(login, state, commit, at)


def changes(*files):
    return ChangeSet("main", tuple(files), {}, {}, {}, frozenset())


def adr(id):
    return f"kb/decisions/{id}.md", f"---\nid: {id}\ntitle: T\ngoverns: [MOD-a]\n---\nbody {id}\n".encode()


def repo_of(files):
    return KbRepo.from_files(files)


def verdict(id, kind, status):
    return Verdict(id, kind, status, "E0", (), (), ())


def test_only_latest_review_on_head_counts():
    cfgv = cfg()
    req = {"@bob": ["changes src/b/x.py"]}
    old = FakeForge([review("bob", commit="d" * 40)])
    assert [i.code for i in owner_approval(old, PR, cfgv, req)] == ["missing_owner_approval"]
    flipped = FakeForge([review("bob"), review("bob", "CHANGES_REQUESTED", at="2026-01-02T00:00:00Z")])
    assert [i.code for i in owner_approval(flipped, PR, cfgv, req)] == ["missing_owner_approval"]
    # a later comment does not cancel an approval
    ok = FakeForge([review("bob"), review("bob", "COMMENTED", at="2026-01-02T00:00:00Z")])
    assert owner_approval(ok, PR, cfgv, req) == []
    # later approval after a rejection counts
    again = FakeForge([review("bob", "CHANGES_REQUESTED"), review("bob", at="2026-01-02T00:00:00Z")])
    assert owner_approval(again, PR, cfgv, req) == []


def test_bot_approval_never_counts():
    forge = FakeForge([review("dependabot[bot]")])
    issues = owner_approval(forge, PR, cfg(), {"@dependabot[bot]": ["x"]})
    assert [i.code for i in issues] == ["missing_owner_approval"]


def test_author_approval_never_counts():
    issues = owner_approval(FakeForge([review("alice")]), PR, cfg(), {"@alice": ["x"]})
    assert [i.code for i in issues] == ["missing_owner_approval"]


def test_message_and_order():
    issues = owner_approval(FakeForge(), PR, cfg(), {"@zed": ["r2"], "@bob": ["r1"]})
    assert [i.message for i in issues] == [
        "@bob has not approved ccccccc (r1)",
        "@zed has not approved ccccccc (r2)",
    ]
    assert owner_approval(FakeForge(), PR, cfg(), {}) == []


def test_team_membership_satisfied_and_unreadable_defers_with_warning():
    forge = FakeForge([review("bob")], teams={"@org/a": {"bob"}})
    defer = cfg(policy={"unreadable_teams": "defer", "approvers": {"intent": ["@pm"], "decisions": ["@arch"]}})
    assert owner_approval(forge, PR, defer, {"@org/a": ["x"]}) == []
    issues = owner_approval(forge, PR, defer, {"@org/b": ["x"], "@org/a": ["y"], "@org/c": ["z"]})
    assert [(i.code, i.severity) for i in issues] == [("owner_approval.deferred", "warning")]
    assert issues[0].message == (
        "cannot read team membership for @org/b, @org/c; relying on branch protection's required code-owner review"
    )


def test_changed_files_use_module_owners(tmp_path):
    req = required_owners(tmp_path, cfg(), repo_of({}), None, changes("src/a/x.py", "src/b/y.py", "README.md"), [])
    assert req == {"@alice-owner": ["changes src/a/x.py"], "@bob": ["changes src/b/y.py"]}


def test_codeowners_last_match_wins(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/CODEOWNERS").write_text("src/** @first\nsrc/a/** @second\n")
    req = required_owners(tmp_path, cfg(), repo_of({}), None, changes("src/a/x.py", "src/b/y.py"), [])
    assert req == {"@second": ["changes src/a/x.py"], "@first": ["changes src/b/y.py"]}


def test_many_paths_collapse(tmp_path):
    files = [f"src/a/{i}.py" for i in range(5)]
    req = required_owners(tmp_path, cfg(), repo_of({}), None, changes(*files), [])
    assert req["@alice-owner"] == ["changes src/a/0.py", "changes src/a/1.py", "changes src/a/2.py", "… and 2 more"]


def test_adr_change_requires_decision_approvers(tmp_path):
    path, data = adr("ADR-0001")
    head = repo_of({path: data})
    assert required_owners(tmp_path, cfg(), head, repo_of({path: data}), changes(), []) == {}
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@arch": ["decision ADR-0001 changed"]}
    base = repo_of({path: data + b"more\n"})
    assert required_owners(tmp_path, cfg(), head, base, changes(), []) == {"@arch": ["decision ADR-0001 changed"]}
    # deleted record
    assert required_owners(tmp_path, cfg(), repo_of({}), base, changes(), []) == {"@arch": ["decision ADR-0001 changed"]}


def test_stale_requirement_requires_intent_approvers(tmp_path):
    head = KbRepo.load(Path(__file__).parent.parent / "fixtures" / "kb_valid")
    rid = min(r for r in head.records if r.startswith("REQ-"))
    req = required_owners(tmp_path, cfg(), head, head, changes(), [verdict(rid, "requirement", "stale")])
    assert req == {"@pm": [f"requirement {rid} is stale"]}
    ok = required_owners(tmp_path, cfg(), head, head, changes(), [verdict(rid, "requirement", "verified")])
    assert ok == {}
    new = required_owners(tmp_path, cfg(), head, None, changes(), [])
    expected = [f"requirement {r} changed" for r in head.records if r.startswith("REQ-")]
    expected += [f"feature {r} changed" for r in head.records if r.startswith("FEAT-")]
    expected += [f"glossary term {r} changed" for r in head.records if r.startswith("TERM-")]
    expected += [f"triage of {s} changed" for s in head.sources]  # with no base, every triage file is new
    assert new["@pm"] == sorted(expected)


def test_team_key_resolves_and_unresolvable_skipped(tmp_path):
    config = cfg(
        teams={"billing": {"git_team": "@org/billing"}},
        modules=[{"id": "MOD-a", "paths": ["src/a/**"], "owner": "billing"}, {"id": "MOD-b", "paths": ["src/b/**"], "owner": "ghost"}],
    )
    req = required_owners(tmp_path, config, repo_of({}), None, changes("src/a/x", "src/b/y"), [])
    assert req == {"@org/billing": ["changes src/a/x"]}


def test_unreadable_team_with_no_approvals_still_fails():
    issues = owner_approval(FakeForge(), PR, cfg(), {"@org/b": ["x"]})
    assert [(i.code, i.severity) for i in issues] == [("missing_owner_approval", "error")]
    # a bot's or the author's approval is not a counting approval
    junk = FakeForge([review("alice"), review("x[bot]")])
    assert [i.code for i in owner_approval(junk, PR, cfg(), {"@org/b": ["x"]})] == ["missing_owner_approval"]


def test_codeowners_github_lookup_order(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/CODEOWNERS").write_text("src/** @gh\n")
    (tmp_path / "CODEOWNERS").write_text("src/** @root\n")
    req = required_owners(tmp_path, cfg(), repo_of({}), None, changes("src/a/x.py"), [])
    assert list(req) == ["@gh"]


def test_rules_taken_earlier_override_the_working_tree(tmp_path):
    from kb.verify.approvals import codeowners_rules

    (tmp_path / "CODEOWNERS").write_text("src/** @first\n")
    rules = codeowners_rules(tmp_path)
    (tmp_path / "CODEOWNERS").write_text("* @mallory\n")
    req = required_owners(tmp_path, cfg(), repo_of({}), None, changes("src/a/x.py"), [], rules=rules)
    assert req == {"@first": ["changes src/a/x.py"]}
    none = required_owners(tmp_path, cfg(), repo_of({}), None, changes("src/a/x.py"), [], rules=None)
    assert none == {"@alice-owner": ["changes src/a/x.py"]}  # no CODEOWNERS when snapshotted: module owners


NO_DECISION_APPROVERS = (
    "kb.config.yaml changed, but the approval policy names no decision approvers; "
    "set policy.approvers.decisions on the default branch first"
)


def policy_cfg(intent=("@pm",), decisions=("@acme/tech-leads",), **extra):
    return cfg(policy={"approvers": {"intent": list(intent), "decisions": list(decisions)}}, **extra)


def test_config_change_needs_decision_approvers(tmp_path):
    req = required_owners(tmp_path, policy_cfg(), repo_of({}), None, changes("kb.config.yaml"), [])
    assert "kb.config.yaml changed" in req["@acme/tech-leads"]


def test_codeowners_change_needs_decision_approvers(tmp_path):
    req = required_owners(tmp_path, policy_cfg(), repo_of({}), None, changes(".github/CODEOWNERS"), [])
    assert ".github/CODEOWNERS changed" in req["@acme/tech-leads"]


def test_ci_workflow_change_needs_decision_approvers(tmp_path):
    """The workflow that runs `kb verify` is part of the gate: a PR that edits it could drop or weaken the check."""
    req = required_owners(tmp_path, policy_cfg(), repo_of({}), None, changes(".github/workflows/kb.yml", ".gitlab-ci.yml"), [])
    assert {".github/workflows/kb.yml changed", ".gitlab-ci.yml changed"} <= set(req["@acme/tech-leads"])
    other = required_owners(tmp_path, policy_cfg(), repo_of({}), None, changes(".github/workflows/other.yml"), [])
    assert "@acme/tech-leads" not in other


def test_moving_codeowners_needs_decision_approval(tmp_path):
    """A real `git mv`: git's rename detection would list only the new path; both count as CODEOWNERS changes."""
    from helpers import commit, git, make_git_repo

    from kb.gitfiles import merge_base
    from kb.verify.changes import compute_changes

    root = make_git_repo(None, tmp_path, {"CODEOWNERS": "/src/ @acme/core\n"})
    git(root, "checkout", "-q", "-b", "feature")
    (root / ".github").mkdir()
    git(root, "mv", "CODEOWNERS", ".github/CODEOWNERS")
    commit(root, "move CODEOWNERS")
    moved = compute_changes(root, merge_base(root, "main"), head_repo=KbRepo.load(root))
    assert set(moved.files) == {"CODEOWNERS", ".github/CODEOWNERS"}
    req = required_owners(tmp_path, policy_cfg(), repo_of({}), None, moved, [], rules=None)
    assert {"CODEOWNERS changed", ".github/CODEOWNERS changed"} <= set(req["@acme/tech-leads"])


def test_config_change_with_no_base_decision_approvers_fails_closed(tmp_path):
    config = policy_cfg(decisions=["TODO"])
    req = required_owners(tmp_path, config, repo_of({}), None, changes("kb.config.yaml"), [])
    assert req == {"(no decision approvers)": ["kb.config.yaml changed"]}
    # nobody's approval satisfies it, not even a login that looks like the sentinel
    forge = FakeForge([review("bob"), review("(no decision approvers)")])
    issues = owner_approval(forge, PR, config, req)
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("missing_owner_approval", "error", NO_DECISION_APPROVERS)
    ]


def test_no_decision_approvers_while_bootstrapping_says_to_set_them_in_this_change(tmp_path):
    """kb.config.yaml is new in this change, so its own policy applies: the fix is in the change, not on the default
    branch."""
    config = policy_cfg(decisions=["TODO"])
    req = required_owners(tmp_path, config, repo_of({}), None, changes("kb.config.yaml"), [])
    issues = owner_approval(FakeForge([review("bob")]), PR, config, req, bootstrap=True)
    assert [i.message for i in issues] == [
        "kb.config.yaml changed, but the approval policy names no decision approvers; "
        "set policy.approvers.decisions in this change"
    ]


def test_emptied_intent_in_the_change_still_needs_base_intent_approvers(tmp_path):
    head = KbRepo.load(Path(__file__).parent.parent / "fixtures" / "kb_valid")
    path = head.paths["REQ-F-001"]
    base = repo_of({**head.raw, path: head.raw[path].replace(b"statement:", b"statement: was", 1)})
    req = required_owners(tmp_path, policy_cfg(intent=["@acme/product"]), head, base, changes(path), [])
    assert "requirement REQ-F-001 changed" in req["@acme/product"]


def test_module_added_in_the_change_owns_nothing_yet(tmp_path):
    """No CODEOWNERS, so module owners route changed files: the base's modules, not one the change adds."""
    import json

    from helpers import commit, git, make_git_repo

    from kb.forge.local import LocalForge
    from kb.verify.run import run_verify

    config = "version: 1\npolicy:\n  approvers:\n    intent: ['@pm']\n    decisions: ['@arch']\n"
    root = make_git_repo(None, tmp_path, {"kb.config.yaml": config})
    git(root, "checkout", "-q", "-b", "feature")
    (root / "kb.config.yaml").write_text(config + "modules:\n- id: MOD-x\n  paths: ['src/**']\n  owner: '@attacker'\n")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("x = 1\n")
    commit(root, "a module of my own")
    forge_path = tmp_path / "forge.json"
    head_sha = git(root, "rev-parse", "HEAD").strip()
    forge_path.write_text(json.dumps({"pr": {"number": 7, "head_sha": head_sha, "author": "mallory"}, "reviews": []}))
    report = run_verify(root, base="main", all_=False, forge=LocalForge(forge_path))
    assert report.required is not None and "@attacker" not in report.required
    assert report.required == {"@arch": ["kb.config.yaml changed"]}


def _team_cfg(git_team="@acme/product", members=("erin",), **policy):
    team = {"git_team": git_team} | ({"members": list(members)} if members is not None else {})
    return cfg(teams={"product": team}, policy={"approvers": {"intent": ["@pm"], "decisions": ["@arch"]}} | policy)


def test_unreadable_team_fails_closed_by_default():
    issues = owner_approval(FakeForge([review("someone")]), PR, cfg(), {"@acme/product": ["x"]})
    assert [(i.code, i.severity) for i in issues] == [("missing_owner_approval", "error")]
    assert "cannot read the members of @acme/product" in issues[0].message
    assert "teams.<name>.members" in issues[0].message
    assert issues[0].message.startswith("@acme/product has not approved ccccccc: ")
    assert issues[0].message.endswith("(x)")


def test_configured_members_used_when_forge_cannot_read():
    c = _team_cfg()
    assert owner_approval(FakeForge([review("erin")]), PR, c, {"@acme/product": ["x"]}) == []
    issues = owner_approval(FakeForge([review("bob")]), PR, c, {"@acme/product": ["x"]})
    assert [i.code for i in issues] == ["missing_owner_approval"]


def test_configured_members_match_case_insensitively():
    c = _team_cfg("@Acme/Product", ["Erin"])
    assert owner_approval(FakeForge([review("erin")]), PR, c, {"@acme/product": ["x"]}) == []


def test_forge_membership_wins_over_config():
    c = _team_cfg()
    teams = {"@acme/product": {"carol"}}
    assert [i.code for i in owner_approval(FakeForge([review("erin")], teams), PR, c, {"@acme/product": ["x"]})] == [
        "missing_owner_approval"
    ]
    assert owner_approval(FakeForge([review("carol")], teams), PR, c, {"@acme/product": ["x"]}) == []


def test_defer_keeps_the_old_behaviour():
    c = _team_cfg(members=None, unreadable_teams="defer")
    issues = owner_approval(FakeForge([review("someone")]), PR, c, {"@acme/product": ["x"]})
    assert [(i.code, i.severity) for i in issues] == [("owner_approval.deferred", "warning")]
    assert issues[0].message == (
        "cannot read team membership for @acme/product; relying on branch protection's required code-owner review"
    )


def test_empty_members_list_still_fails_closed():
    c = _team_cfg(members=[])
    issues = owner_approval(FakeForge([review("erin")]), PR, c, {"@acme/product": ["x"]})
    assert [(i.code, i.severity) for i in issues] == [("missing_owner_approval", "error")]
    assert "cannot read the members of @acme/product" in issues[0].message


def _only(path, data=b"x: 1\n"):
    return repo_of({path: data})


def test_feature_change_requires_intent_approvers(tmp_path):
    head = _only("kb/features/FEAT-001.md", b"---\nid: FEAT-001\ntitle: T\nmodule: MOD-a\n---\nbody\n")
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@pm": ["feature FEAT-001 changed"]}


def test_glossary_change_requires_intent_approvers(tmp_path):
    head = _only("kb/glossary/TERM-001.yaml", b"id: TERM-001\nname: Seat\ndefinition: A seat.\n")
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@pm": ["glossary term TERM-001 changed"]}


def test_component_change_requires_decision_approvers(tmp_path):
    head = _only("kb/components/COMP-001.md", b"---\nid: COMP-001\ntitle: C\nmodule: MOD-a\n---\nbody\n")
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@arch": ["component COMP-001 changed"]}


def test_unparsable_changed_record_still_needs_approvers(tmp_path):
    files = {
        "kb/features/FEAT-009.md": b"---\nnot: [valid\n---\n",
        "kb/glossary/TERM-009.yaml": b"id: [\n",
        "kb/components/COMP-009.md": b"---\n: :\n---\n",
        "kb/features/FEAT-001.md": b"---\nid: FEAT-001\ntitle: T\nmodule: MOD-a\n---\nbody\n",
    }
    head = repo_of(files)
    assert "kb/features/FEAT-009.md" not in head.paths.values()
    got = required_owners(tmp_path, cfg(), head, repo_of({}), changes(*files), [])
    assert got == {
        "@arch": ["kb/components/COMP-009.md changed"],
        "@pm": ["feature FEAT-001 changed", "kb/features/FEAT-009.md changed", "kb/glossary/TERM-009.yaml changed"],
    }
    assert required_owners(tmp_path, cfg(), head, None, changes(*files), []) == {
        "@pm": ["feature FEAT-001 changed"]}  # no base: changed files are not consulted


def test_empty_approver_groups_with_changed_records_warn_once(tmp_path):
    from kb.verify.approvals import no_approver_warnings

    head = KbRepo.load(Path(__file__).parent.parent / "fixtures" / "kb_valid")
    req = head.paths["REQ-F-001"]
    base_files = {**head.raw, req: head.raw[req].replace(b"statement:", b"statement: was", 1)}
    for path in [p for p in head.raw if p.startswith("kb/glossary/")][:1]:
        base_files[path] = head.raw[path] + b"\n"  # a second intent record: still one warning
    for path in [p for p in head.raw if p.startswith("kb/decisions/")][:1]:
        base_files[path] = head.raw[path] + b"\n"
    base = repo_of(base_files)
    files = changes(*(p for p in base_files if base_files[p] != head.raw.get(p)))
    empty = policy_cfg(intent=[], decisions=["TODO"])
    warnings = no_approver_warnings(empty, head, base, files, [])
    assert [(i.code, i.severity, i.message) for i in warnings] == [
        ("owner_approval.no_approvers", "warning", "intent approvers are empty in the approval policy; changes to "
         "requirements, exclusions, features, glossary terms, triage, evaluation decisions and baselines need no approval"),
        ("owner_approval.no_approvers", "warning", "decisions approvers are empty in the approval policy; changes to "
         "decisions, invariants and components need no approval"),
    ]
    assert no_approver_warnings(policy_cfg(), head, base, files, []) == []  # both groups named
    assert no_approver_warnings(empty, head, repo_of(head.raw), changes(), []) == []  # no record changed
    stale = no_approver_warnings(empty, head, repo_of(head.raw), changes(), [verdict("REQ-F-001", "requirement", "stale")])
    assert [i.message.split(" ", 1)[0] for i in stale] == ["intent"]  # a stale requirement needs intent approval too


def test_triage_change_needs_intent_approvers(tmp_path):
    from kb.verify.approvals import no_approver_warnings

    path = "kb/sources/SRC-spec/triage.yaml"
    old, new = b"segments:\n  seg-01: [REQ-F-001]\n", b"segments:\n  seg-01: 'none: heading'\n"
    head, base = repo_of({path: new}), repo_of({path: old})
    assert required_owners(tmp_path, cfg(), head, base, changes(path), []) == {"@pm": ["triage of SRC-spec changed"]}
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@pm": ["triage of SRC-spec changed"]}
    assert required_owners(tmp_path, cfg(), head, repo_of({path: new}), changes(), []) == {}
    # deleted, or no longer parsing: still the intent approvers
    assert required_owners(tmp_path, cfg(), repo_of({}), base, changes(path), []) == {"@pm": ["triage of SRC-spec changed"]}
    broken = repo_of({path: b"segments: [oops\n"})
    assert required_owners(tmp_path, cfg(), broken, base, changes(path), []) == {"@pm": ["triage of SRC-spec changed"]}
    # other stored source files are written by kb, not approved
    text = "kb/sources/SRC-spec/text.md"
    assert required_owners(tmp_path, cfg(), repo_of({text: b"x"}), repo_of({}), changes(text), []) == {}
    empty = cfg(policy={"approvers": {"intent": [], "decisions": ["@arch"]}})
    assert [i.code for i in no_approver_warnings(empty, head, base, changes(path), [])] == ["owner_approval.no_approvers"]


EXCL = b"id: EXCL-001\nstatement: S\nevidence:\n  quotes:\n  - source: SRC-a#seg-01\n    text: Some quoted text.\nmodules: [MOD-a]\n"


def test_exclusion_change_requires_intent_approvers(tmp_path):
    from kb.verify.approvals import no_approver_warnings

    path = "kb/exclusions/EXCL-001.yaml"
    head = _only(path, EXCL)
    assert required_owners(tmp_path, cfg(), head, None, changes(), []) == {"@pm": ["exclusion EXCL-001 changed"]}
    base = repo_of({path: EXCL.replace(b"statement: S", b"statement: Was")})
    assert required_owners(tmp_path, cfg(), head, base, changes(path), []) == {"@pm": ["exclusion EXCL-001 changed"]}
    # one that does not parse is routed by its directory
    broken = repo_of({path: b"id: [\n"})
    assert required_owners(tmp_path, cfg(), broken, repo_of({}), changes(path), []) == {"@pm": [f"{path} changed"]}
    (w,) = no_approver_warnings(policy_cfg(intent=[]), head, repo_of({}), changes(path), [])
    assert w.message == ("intent approvers are empty in the approval policy; changes to requirements, exclusions, features, "
                         "glossary terms, triage, evaluation decisions and baselines need no approval")
