import os
import shutil
import subprocess
import sys
from pathlib import Path

from kb.verify.report import STATUS_PATH


def run_verify(repo: Path, env: dict[str, str]) -> tuple[bytes, bytes]:
    kb = Path(sys.executable).parent / "kb"
    done = subprocess.run(
        [str(kb), "-C", str(repo), "verify", "--all", "--format", "json", "--forge", "none"],
        env=env, capture_output=True, check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    return done.stdout, (repo / STATUS_PATH).read_bytes()


def test_verify_json_identical_under_actions_env_and_plain(verify_repo, tmp_path):
    path = f"{Path(sys.executable).parent}:{os.environ['PATH']}"
    one, two = tmp_path / "h1", tmp_path / "h2"
    one.mkdir()
    two.mkdir()
    actions = {"PATH": path, "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_TEMP": str(one), "TZ": "UTC",
               "LANG": "C.UTF-8", "HOME": str(one)}
    plain = {"PATH": path, "HOME": str(two), "TZ": "Asia/Karachi"}
    out_a, status_a = run_verify(verify_repo, actions)
    shutil.rmtree(verify_repo / ".kb/cache")
    out_b, status_b = run_verify(verify_repo, plain)
    assert out_a and out_a == out_b
    assert status_a == status_b
