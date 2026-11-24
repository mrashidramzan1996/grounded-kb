import sys
from pathlib import Path

import pytest

from kb.config.load import load_config
from kb.config.schema import KbConfig, TestsCfg
from kb.testrun import PYTEST_PRESET, CommandRunner, TestRunError, parse_junit, run_tests, runner_for

PROJECT = {
    "tests/test_a.py": '''
import pathlib
import pytest

def test_pass():
    assert True

def test_fail():
    assert False

@pytest.mark.skip(reason="no")
def test_skip():
    pass

@pytest.mark.parametrize("n", [1, 2])
def test_param_ok(n):
    assert n

@pytest.mark.parametrize("n", [1, 2])
def test_param_bad(n):
    assert n == 1

class TestSeats:
    def test_x(self):
        assert True

def test_flaky():
    flag = pathlib.Path(__file__).parent.parent / ".flag"
    if not flag.exists():
        flag.write_text("x")
        assert False
''',
    "record_argv.py": '''
import json, sys
open("argv.json", "w").write(json.dumps(sys.argv[1:]))
''',
}
A = "tests/test_a.py::"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for rel, text in PROJECT.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}:{__import__('os').environ['PATH']}")
    return tmp_path


def _runner(**kw):
    return CommandRunner(TestsCfg(preset="pytest", **kw))


def test_pytest_preset_outcomes(project):
    refs = [A + "test_pass", A + "test_fail", A + "test_skip", A + "TestSeats::test_x", A + "test_nope"]
    r = _runner().run(project, refs)
    assert r.outcomes == {A + "test_pass": "passed", A + "test_fail": "failed", A + "test_skip": "skipped",
                          A + "TestSeats::test_x": "passed", A + "test_nope": "missing"}


def test_test_run_leaves_no_pycache(project, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    (project / "tests/helper_mod.py").write_text("X = 1\n")
    (project / "tests/test_imp.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
                                               "import helper_mod\n\ndef test_imp():\n    assert helper_mod.X\n")
    assert _runner().run(project, ["tests/test_imp.py::test_imp"]).outcomes == {"tests/test_imp.py::test_imp": "passed"}
    assert list(project.rglob("__pycache__")) == []


def test_parametrized_cases_all_must_pass(project):
    r = _runner().run(project, [A + "test_param_ok", A + "test_param_bad"])
    assert r.outcomes == {A + "test_param_ok": "passed", A + "test_param_bad": "failed"}


def test_flaky_detected_after_rerun(project):
    r = _runner().run(project, [A + "test_flaky", A + "test_pass"])
    assert r.outcomes == {A + "test_flaky": "flaky", A + "test_pass": "passed"}
    assert (project / ".flag").exists()


def test_no_rerun_when_reruns_zero(project):
    assert run_tests(project, _cfg(), {"MOD-a": [A + "test_flaky"]}, reruns=0) == {A + "test_flaky": "failed"}


def test_test_ids_are_separate_argv_items(project):
    import json
    cfg = TestsCfg(run="python record_argv.py {tests} --junitxml={junit}")
    refs = ["tests/a b.py::test_x; touch pwned", "t.py::$(id)"]
    r = CommandRunner(cfg).run(project, refs)
    argv = json.loads((project / "argv.json").read_text())
    assert argv[:2] == refs and argv[2].startswith("--junitxml=") and len(argv) == 3
    assert not (project / "pwned").exists() and set(r.outcomes.values()) == {"failed"}


def test_test_env_has_no_tokens(project, monkeypatch):
    """The project's tests are untrusted code: they never see the forge or Actions credentials."""
    import json
    tokens = ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
              "ACTIONS_ID_TOKEN_REQUEST_URL")
    for name in tokens:
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("KB_KEEP_ME", "1")
    (project / "record_env.py").write_text("import json, os\nopen('env.json', 'w').write(json.dumps(dict(os.environ)))\n")
    CommandRunner(TestsCfg(run="python record_env.py {tests} --junitxml={junit}")).run(project, ["a.py::f"])
    env = json.loads((project / "env.json").read_text())
    assert not set(tokens) & set(env) and "secret-value" not in env.values()
    assert env["KB_KEEP_ME"] == "1"


def test_id_format(project):
    import json
    cfg = TestsCfg(run="python record_argv.py {tests} --junitxml={junit}", id_format="{path}#{name}")
    CommandRunner(cfg).run(project, ["a.py::C::f"])
    assert json.loads((project / "argv.json").read_text())[0] == "a.py#C::f"


def test_timeout_marks_all_failed(project):
    cfg = TestsCfg(run="python -c 'import time; time.sleep(30)' {tests} --junitxml={junit}", timeout_seconds=1)
    r = CommandRunner(cfg).run(project, [A + "test_pass", A + "test_fail"])
    assert set(r.outcomes.values()) == {"failed"} and "timed out" in r.log_tail


def test_missing_executable_raises(project):
    with pytest.raises(TestRunError):
        CommandRunner(TestsCfg(run="no-such-binary-xyz {tests} {junit}")).run(project, [A + "test_pass"])


def test_unparseable_junit_fails_all():
    assert parse_junit(b"<not xml", ["a.py::f"]) == {"a.py::f": "failed"}


def test_parse_junit_matches_by_classname_without_file():
    xml = b'<testsuite><testcase classname="tests.test_a.TestSeats" name="test_x[1]"/></testsuite>'
    assert parse_junit(xml, ["tests/test_a.py::TestSeats::test_x", "tests/test_a.py::test_x"]) == {
        "tests/test_a.py::TestSeats::test_x": "passed", "tests/test_a.py::test_x": "missing"}


def _xml(*cases: str) -> bytes:
    return ("<testsuite>" + "".join(cases) + "</testsuite>").encode()


def test_same_name_in_two_classes_kept_apart():
    """xunit1 sets `file` on every case: the class part must still match, or TestB's pass would hide TestA's skip."""
    xml = _xml(
        '<testcase file="tests/test_a.py" classname="tests.test_a.TestA" name="test_init"><skipped/></testcase>',
        '<testcase file="tests/test_a.py" classname="tests.test_a.TestB" name="test_init"/>',
        '<testcase file="tests/test_a.py" classname="tests.test_a.Outer.Inner" name="test_init"/>',
    )
    refs = [A + "TestA::test_init", A + "TestB::test_init", A + "Outer::Inner::test_init", A + "Inner::test_init"]
    assert parse_junit(xml, refs) == {A + "TestA::test_init": "skipped", A + "TestB::test_init": "passed",
                                      A + "Outer::Inner::test_init": "passed", A + "Inner::test_init": "missing"}


def test_module_level_ref_not_matched_by_class_method():
    xml = _xml('<testcase file="tests/test_a.py" classname="tests.test_a.TestA" name="test_x"/>')
    assert parse_junit(xml, [A + "test_x"]) == {A + "test_x": "missing"}
    xml = _xml('<testcase file="tests/test_a.py" classname="tests.test_a" name="test_x"/>')
    assert parse_junit(xml, [A + "test_x", A + "TestA::test_x"]) == {A + "test_x": "passed", A + "TestA::test_x": "missing"}


def test_skipped_case_makes_parametrised_ref_skipped():
    """Skipped counts as not passing: one skipped case and no failure make the whole ref skipped."""
    xml = _xml(
        '<testcase file="tests/test_a.py" classname="tests.test_a" name="test_p[1]"/>',
        '<testcase file="tests/test_a.py" classname="tests.test_a" name="test_p[2]"><skipped/></testcase>',
        '<testcase file="tests/test_a.py" classname="tests.test_a" name="test_q[1]"><failure/></testcase>',
        '<testcase file="tests/test_a.py" classname="tests.test_a" name="test_q[2]"><skipped/></testcase>',
    )
    assert parse_junit(xml, [A + "test_p", A + "test_q"]) == {A + "test_p": "skipped", A + "test_q": "failed"}


def _cfg(tests=None):
    return KbConfig(version=1, modules=[{"id": "MOD-a", "owner": "t", "tests": tests or {"preset": "pytest"}},
                                        {"id": "MOD-b", "owner": "t"}])


def test_runner_for():
    c = _cfg()
    assert runner_for(c, "MOD-a").cfg.preset == "pytest"
    assert runner_for(c, "MOD-b") is None and runner_for(c, "MOD-zzz") is None and runner_for(c, None) is None
    assert "{tests}" in PYTEST_PRESET


def test_run_tests_no_runner_and_merge(project):
    c = _cfg()
    out = run_tests(project, c, {"MOD-a": [A + "test_pass", A + "test_fail"], "MOD-b": [A + "test_pass"],
                                 None: ["x.py::y"]})
    assert out == {A + "test_pass": "passed", A + "test_fail": "failed", "x.py::y": "no_runner"}


def test_worse_outcome_wins_across_two_runner_modules(project):
    (project / "fail_stub.py").write_text(
        "import sys\n"
        "out = [a for a in sys.argv if a.startswith('--junitxml=')][0].split('=', 1)[1]\n"
        "open(out, 'w').write('<testsuite><testcase classname=\"tests.test_a\" name=\"test_pass\"><failure/></testcase></testsuite>')\n")
    c = KbConfig(version=1, modules=[
        {"id": "MOD-a", "owner": "t", "tests": {"preset": "pytest"}},
        {"id": "MOD-b", "owner": "t", "tests": {"run": "python fail_stub.py {tests} --junitxml={junit}"}}])
    assert run_tests(project, c, {"MOD-a": [A + "test_pass"], "MOD-b": [A + "test_pass"]}, reruns=0) == {A + "test_pass": "failed"}


def test_empty_refs_runs_nothing(project):
    assert _runner().run(project, []).outcomes == {}
    assert run_tests(project, _cfg(), {"MOD-a": []}) == {}
    assert not (project / ".flag").exists()


def test_non_executable_command_raises_test_run_error(project):
    (project / "noexec.sh").write_text("#!/bin/sh\n")
    with pytest.raises(TestRunError):
        CommandRunner(TestsCfg(run="./noexec.sh {tests} {junit}")).run(project, [A + "test_pass"])


def test_tests_cfg_needs_exactly_one_of_preset_or_run():
    for kw in ({}, {"preset": "pytest", "run": "x {tests} {junit}"}):
        with pytest.raises(ValueError):
            TestsCfg(**kw)


def test_run_template_needs_tests_and_junit_tokens():
    for run in ("pytest {tests}", "pytest --junitxml={junit}"):
        with pytest.raises(ValueError):
            TestsCfg(run=run)


def test_run_template_tests_token_must_stand_alone():
    with pytest.raises(ValueError, match="separate argument"):
        TestsCfg(run="pytest --ids={tests} --junitxml={junit}")


def test_unknown_tests_key_rejected(tmp_path):
    from kb.config.load import ConfigError
    (p := tmp_path / "kb.config.yaml").write_text(
        "version: 1\nmodules: [{id: MOD-a, owner: t, tests: {preset: pytest, runn: x}}]\n")
    with pytest.raises(ConfigError, match="runn"):
        load_config(p)
