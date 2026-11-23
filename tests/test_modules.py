from helpers import codes, ctx, invoke

from kb.config.load import load_config
from kb.gates.base import GateContext
from kb.gitfiles import repo_files
from kb.modules import module_paths_gate, suggest_paths


def test_suggest_from_markers(built_acme):  # services/billing/{seats,invoices,proration}.py carry r[impl REQ-F-00x] of MOD-billing
    assert suggest_paths(*ctx(built_acme)) == {"MOD-billing": ["services/billing/**"]}


def test_suggest_spans_directories(built_acme_with_web_impl):
    assert suggest_paths(*ctx(built_acme_with_web_impl))["MOD-billing"] == ["services/billing/**", "web/billing/**"]


def test_no_evidence_no_suggestion(acme_repo_ingested):
    assert suggest_paths(*ctx(acme_repo_ingested)) == {}


def test_accept_writes_paths(built_acme):
    assert invoke(built_acme, "module", "paths", "MOD-billing", "--accept").exit_code == 0
    assert load_config(built_acme / "kb.config.yaml").modules[0].paths == ["services/billing/**"]


def _write(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_sibling_dirs_merge_to_common_ancestor(built_acme):
    _write(built_acme, {"services/billing/sub/deep.py": "# r[impl REQ-F-001]\n"})
    assert suggest_paths(*ctx(built_acme))["MOD-billing"] == ["services/billing/**"]


def test_merge_stops_at_repo_root(built_acme):
    _write(built_acme, {"lib/x/a.py": "# r[impl REQ-F-001]\n", "app/y/b.py": "# r[impl REQ-F-001]\n"})
    assert suggest_paths(*ctx(built_acme))["MOD-billing"] == ["app/y/**", "lib/x/**", "services/billing/**"]


def test_no_merge_over_another_modules_files(built_acme):
    _write(
        built_acme,
        {
            "pkg/a/x.py": "# r[impl REQ-F-001]\n",
            "pkg/b/y.py": "# r[impl REQ-F-001]\n",
            "pkg/c/z.py": "# r[impl REQ-F-004]\n",  # MOD-web
        },
    )
    s = suggest_paths(*ctx(built_acme))
    assert "pkg/a/**" in s["MOD-billing"] and "pkg/b/**" in s["MOD-billing"] and "pkg/**" not in s["MOD-billing"]
    assert s["MOD-web"] == ["pkg/c/**"]


def test_no_merge_over_another_modules_configured_paths(built_acme):
    cfg = built_acme / "kb.config.yaml"
    cfg.write_text(cfg.read_text().replace("{id: MOD-web, owner:", "{id: MOD-web, paths: ['pkg/c/**'], owner:"))
    _write(
        built_acme,
        {"pkg/a/x.py": "# r[impl REQ-F-001]\n", "pkg/b/y.py": "# r[impl REQ-F-001]\n", "pkg/c/z.py": "print()\n"},
    )
    got = suggest_paths(*ctx(built_acme))["MOD-billing"]
    assert "pkg/**" not in got and "pkg/a/**" in got


def test_root_level_and_hidden_and_kb_files_give_no_suggestion(built_acme):
    for p in (built_acme / "services").rglob("*.py"):
        p.unlink()
    _write(
        built_acme,
        {"top.py": "# r[impl REQ-F-001]\n", ".hidden/a.py": "# r[impl REQ-F-001]\n", "kb/notes/n.py": "# r[impl REQ-F-001]\n"},
    )
    assert suggest_paths(*ctx(built_acme)) == {}


def test_verify_markers_are_not_evidence(built_acme):
    for p in (built_acme / "services").rglob("*.py"):
        p.write_text(p.read_text().replace("impl", "verify"))
    assert suggest_paths(*ctx(built_acme)) == {}


def test_requirement_belongs_through_feature_module(built_acme):
    (built_acme / "kb/features").mkdir(parents=True, exist_ok=True)
    (built_acme / "kb/features/FEAT-001.md").write_text(
        "---\nid: FEAT-001\ntitle: Seats\nmodule: MOD-sessions\nrequirements: [REQ-F-001]\n---\nbody\n"
    )
    (built_acme / "services/billing/seats.py").write_text("def add_seat(): ...\n")
    _write(built_acme, {"sess/core/s.py": "# r[impl REQ-F-001]\n"})
    req = built_acme / "kb/requirements/REQ-F-001.yaml"
    req.write_text(req.read_text().replace("modules:\n- MOD-billing\n", "features:\n- FEAT-001\n"))
    s = suggest_paths(*ctx(built_acme))
    assert s["MOD-sessions"] == ["sess/core/**"] and s["MOD-billing"] == ["services/billing/**"]


def test_claim_anchor_file_is_evidence(built_acme):
    _write(built_acme, {"engine/core/calc.py": "def total(): ...\n"})
    (built_acme / "kb/features").mkdir(parents=True, exist_ok=True)
    (built_acme / "kb/features/FEAT-002.md").write_text(
        "---\nid: FEAT-002\ntitle: Calc\nmodule: MOD-sessions\nclaims:\n"
        "  - {id: FEAT-002.C1, claim: totals, code: 'engine/core/calc.py#total'}\n---\nbody\n"
    )
    assert suggest_paths(*ctx(built_acme))["MOD-sessions"] == ["engine/core/**"]


def test_current_paths_are_not_suggested_again(built_acme):
    assert invoke(built_acme, "module", "paths", "MOD-billing", "--accept").exit_code == 0
    assert suggest_paths(*ctx(built_acme)) == {}


def test_gate_message(built_acme):
    root, config, repo, _ = ctx(built_acme)
    (issue,) = module_paths_gate(GateContext(root, config, repo))
    assert (issue.code, issue.severity, issue.record) == ("module.paths_suggested", "warning", "MOD-billing")
    assert issue.message == (
        "MOD-billing: implemented in services/billing/ (3 files) → kb module paths MOD-billing --accept"
    )


def test_gate_silent_without_evidence(acme_repo_ingested):
    root, config, repo, _ = ctx(acme_repo_ingested)
    assert codes(module_paths_gate(GateContext(root, config, repo))) == []


def test_cli_accept_without_suggestion_exits_1(acme_repo_ingested):
    add = invoke(acme_repo_ingested, "module", "add", "MOD-x", "--owner", "@a/b")
    assert add.exit_code == 0
    r = invoke(acme_repo_ingested, "module", "paths", "MOD-x", "--accept")
    assert r.exit_code == 1 and "no suggestion" in r.output.lower()


def test_cli_set_and_list(acme_repo_ingested):
    assert invoke(acme_repo_ingested, "module", "add", "MOD-x", "--owner", "@a/b", "--title", "X").exit_code == 0
    assert invoke(acme_repo_ingested, "module", "add", "MOD-y", "--owner", "@a/b").exit_code == 0
    assert invoke(acme_repo_ingested, "module", "paths", "MOD-x", "--set", "a/**", "--set", "b/**").exit_code == 0
    out = invoke(acme_repo_ingested, "module", "list").output
    assert "MOD-x" in out and "a/**" in out and "b/**" in out
    assert "MOD-y" in out and "(planned: no paths yet)" in out


def test_cli_errors_exit_2(acme_repo_ingested):
    assert invoke(acme_repo_ingested, "module", "paths", "MOD-nope", "--set", "a/**").exit_code == 2
    assert invoke(acme_repo_ingested, "module", "paths", "MOD-nope", "--accept").exit_code == 2
    assert invoke(acme_repo_ingested, "module", "add", "bad", "--owner", "@a/b").exit_code == 2
    assert invoke(acme_repo_ingested, "module", "paths", "MOD-x").exit_code == 2
    assert invoke(acme_repo_ingested, "module", "add", "MOD-a", "--owner", "@a/b").exit_code == 0
    assert invoke(acme_repo_ingested, "module", "add", "MOD-a", "--owner", "@a/b").exit_code == 2
    assert invoke(acme_repo_ingested, "module", "paths", "MOD-a", "--accept", "--set", "x/**").exit_code == 2


def test_unconfigured_module_gets_no_suggestion(built_acme):
    _write(built_acme, {"x/y/z.py": "# r[impl FEAT-009]\n"})
    (built_acme / "kb/features").mkdir(parents=True, exist_ok=True)
    (built_acme / "kb/features/FEAT-009.md").write_text("---\nid: FEAT-009\ntitle: T\nmodule: MOD-ghost\n---\nb\n")
    assert "MOD-ghost" not in suggest_paths(*ctx(built_acme))
