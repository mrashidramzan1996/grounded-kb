from pathlib import Path

from kb.config.schema import KbConfig
from kb.forge.local import LocalForge  # noqa: F401
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
    assert owner_approval(forge, PR, cfg(), {"@org/a": ["x"]}) == []
    issues = owner_approval(forge, PR, cfg(), {"@org/b": ["x"], "@org/a": ["y"], "@org/c": ["z"]})
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
    assert new["@pm"] == sorted(f"requirement {r} changed" for r in head.records if r.startswith("REQ-"))


def test_team_key_resolves_and_unresolvable_skipped(tmp_path):
    config = cfg(
        teams={"billing": {"git_team": "@org/billing"}},
        modules=[{"id": "MOD-a", "paths": ["src/a/**"], "owner": "billing"}, {"id": "MOD-b", "paths": ["src/b/**"], "owner": "ghost"}],
    )
    req = required_owners(tmp_path, config, repo_of({}), None, changes("src/a/x", "src/b/y"), [])
    assert req == {"@org/billing": ["changes src/a/x"]}
