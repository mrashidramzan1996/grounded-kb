from kb.config.validate import modules_for, resolve_owner, validate_config
from tests.helpers import codes, mod


def test_owner_forms(cfg):
    c = cfg(modules=[mod("MOD-a", owner="team-a"), mod("MOD-b", owner="@acme/b"), mod("MOD-c", owner="ghost"), mod("MOD-d", owner="TODO")])
    assert codes(validate_config(c, [])) == ["config.unknown_owner", "config.owner_todo"]


def test_docs_only_repo_has_no_coverage_issues(cfg):
    c = cfg(modules=[mod("MOD-billing")], sources=["docs/**"])
    assert validate_config(c, ["docs/pricing-spec-v3.pdf", "README.md"]) == []


def test_two_modules_error_unassigned_code_warning(cfg):
    c = cfg(modules=[mod("MOD-svc", ["services/**"]), mod("MOD-billing", ["services/billing/**"])])
    issues = validate_config(c, ["services/billing/a.py", "lib/x.py", "notes.txt"])
    assert [(i.code, i.severity, i.path) for i in issues] == [
        ("config.file_unassigned", "warning", "lib/x.py"),
        ("config.file_in_two_modules", "error", "services/billing/a.py"),
    ]


def test_duplicate_module_and_approver_todo(cfg):
    c = cfg(modules=[mod("MOD-a"), mod("MOD-a")], policy={"approvers": {"intent": ["TODO"], "decisions": ["nobody"]}})
    assert codes(validate_config(c, [])) == ["config.duplicate_module", "config.owner_todo", "config.unknown_owner"]


def test_exempt_files(cfg):
    c = cfg(modules=[mod("MOD-a", ["src/**"])], ignore=["vendor/**"], generated=["gen/**"])
    files = ["kb/x.py", "setup.py", ".github/a.py", "vendor/v.py", "gen/g.py", "src/ok.py"]
    assert [i.path for i in validate_config(c, files)] == ["setup.py"]


def test_resolve_owner_and_modules_for(cfg):
    c = cfg(modules=[mod("MOD-a", ["a/**"]), mod("MOD-b")])
    assert resolve_owner("team-a", c) == "@acme/a" and resolve_owner("@x/y", c) == "@x/y" and resolve_owner("ghost", c) is None
    assert modules_for("a/f.py", c) == ["MOD-a"] and modules_for("b/f.py", c) == []


def test_test_files_need_no_module(cfg):
    c = cfg(modules=[mod("MOD-a", ["src/**"])])
    files = ["tests/test_a.py", "pkg/test_b.py", "pkg/b_test.go", "web/a.test.ts", "web/a.spec.ts",
             "web/__tests__/x.ts", "spec/x.rb", "lib/real.py"]
    assert [i.path for i in validate_config(c, files)] == ["lib/real.py"]
