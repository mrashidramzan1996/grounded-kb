import os
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from kb.config.schema import KbConfig, TestsCfg
from kb.gitfiles import SCRUBBED_GIT_ENV
from kb.ports.tests import Outcome, TestRun

# Runs the project's own `python` from PATH, never kb's interpreter.
PYTEST_PRESET = "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 {tests} --junitxml={junit}"
_LOG_TAIL = 4000
_WORSE: list[Outcome] = ["failed", "skipped", "missing", "flaky", "passed", "no_runner"]
# Credentials the project's tests (untrusted code on a PR) must never see.
SCRUBBED_TOKEN_ENV = frozenset({
    "GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
})


class TestRunError(Exception):
    __test__ = False


def _split_ref(ref: str) -> tuple[str, str]:
    path, _, name = ref.partition("::")
    return path, name


def parse_junit(xml: bytes, refs: list[str]) -> dict[str, Outcome]:
    """Match each `path::[Class::]func` ref to junit testcases. Unparseable XML fails every ref."""
    try:
        cases = list(ET.fromstring(xml).iter("testcase"))
    except ET.ParseError:
        return {r: "failed" for r in refs}
    out: dict[str, Outcome] = {}
    for ref in refs:
        path, name = _split_ref(ref)
        *classes, func = name.split("::")
        classname = path.removesuffix(".py").replace("/", ".")
        if classes:
            classname += "." + ".".join(classes)
        states: list[str] = []
        for c in cases:
            if c.get("name", "").split("[", 1)[0] != func:
                continue
            if c.get("file") != path and c.get("classname") != classname:
                continue
            if c.find("failure") is not None or c.find("error") is not None:
                states.append("failed")
            elif c.find("skipped") is not None:
                states.append("skipped")
            else:
                states.append("passed")
        if not states:
            out[ref] = "missing"
        elif "failed" in states:
            out[ref] = "failed"
        elif "passed" in states:
            out[ref] = "passed"
        else:
            out[ref] = "skipped"
    return out


class CommandRunner:
    """Runs a command template (no shell); `{tests}` expands to one argv item per test id."""

    def __init__(self, cfg: TestsCfg, reruns: int = 2) -> None:
        self.cfg = cfg
        self.reruns = reruns
        self.template = PYTEST_PRESET if cfg.preset == "pytest" else (cfg.run or "")

    def _format(self, ref: str) -> str:
        path, name = _split_ref(ref)
        return self.cfg.id_format.replace("{path}", path).replace("{name}", name)

    def _once(self, root: Path, refs: list[str]) -> tuple[dict[str, Outcome], str, bool]:
        """(outcomes, log tail, whether a junit report was produced)."""
        env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_GIT_ENV and k not in SCRUBBED_TOKEN_ENV}
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # no __pycache__/ left behind: a run must not dirty the tree
        with tempfile.TemporaryDirectory(prefix="kb-junit-") as tmp:
            junit = Path(tmp) / "junit.xml"
            argv: list[str] = []
            for tok in shlex.split(self.template):
                if tok == "{tests}":
                    argv += [self._format(r) for r in refs]
                else:
                    argv.append(tok.replace("{junit}", str(junit)))
            try:
                p = subprocess.run(argv, cwd=root, timeout=self.cfg.timeout_seconds, capture_output=True, env=env, check=False)
            except OSError as e:
                raise TestRunError(f"cannot run test command {argv[0]!r}: {e}") from e
            except subprocess.TimeoutExpired:
                return {r: "failed" for r in refs}, f"test command timed out after {self.cfg.timeout_seconds}s", False
            log = (p.stdout + p.stderr).decode(errors="replace")[-_LOG_TAIL:]
            if not junit.is_file():
                return {r: "failed" for r in refs}, f"test command wrote no junit report (exit {p.returncode})\n{log}"[-_LOG_TAIL:], False
            return parse_junit(junit.read_bytes(), refs), log, True

    def run(self, root: Path, refs: list[str]) -> TestRun:
        if not refs:
            return TestRun(outcomes={}, log_tail="")
        outcomes, log, reported = self._once(root, refs)
        if reported and len(refs) > 1 and set(outcomes.values()) == {"missing"}:
            # pytest aborts the whole run on one unknown node id and reports nothing: retry each id alone
            outcomes = {r: self._once(root, [r])[0][r] for r in refs}
        if reported:
            for ref in [r for r, o in outcomes.items() if o == "failed"]:
                for _ in range(self.reruns):
                    if self._once(root, [ref])[0][ref] == "passed":
                        outcomes[ref] = "flaky"
                        break
        return TestRun(outcomes=outcomes, log_tail=log)


def runner_for(config: KbConfig, module_id: str | None) -> CommandRunner | None:
    for m in config.modules:
        if m.id == module_id and m.tests is not None:
            return CommandRunner(m.tests)
    return None


def run_tests(root: Path, config: KbConfig, refs_by_module: dict[str | None, list[str]], reruns: int = 2) -> dict[str, Outcome]:
    merged: dict[str, Outcome] = {}

    def put(ref: str, o: Outcome) -> None:
        if ref not in merged or _WORSE.index(o) < _WORSE.index(merged[ref]):
            merged[ref] = o

    for module_id, refs in refs_by_module.items():
        refs = sorted(set(refs))
        if not refs:
            continue
        runner = runner_for(config, module_id)
        if runner is None:
            for r in refs:
                put(r, "no_runner")
            continue
        runner.reruns = reruns
        for r, o in runner.run(root, refs).outcomes.items():
            put(r, o)
    return merged
