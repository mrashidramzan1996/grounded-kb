import json

import pytest

from kb.forge import ForgeError, forge_from_option
from kb.forge.local import LocalForge
from kb.ports.forge import COMMENT_MARKER, PullRequest, Review

PR = PullRequest(7, "a" * 40, "alice")


def write(tmp_path, data):
    path = tmp_path / "forge.json"
    path.write_text(json.dumps(data))
    return path


def test_local_forge_reads_pr_reviews_teams(tmp_path):
    path = write(tmp_path, {
        "pr": {"number": 7, "head_sha": "a" * 40, "author": "alice"},
        "reviews": [{"login": "bob", "state": "APPROVED", "commit_id": "a" * 40, "submitted_at": "2026-01-01T00:00:00Z"}],
        "teams": {"@org/x": ["bob"]},
        "comments": [],
    })
    forge = LocalForge(path)
    assert forge.pull_request() == PR
    assert forge.reviews(PR) == [Review("bob", "APPROVED", "a" * 40, "2026-01-01T00:00:00Z")]
    assert forge.team_members("@org/x") == {"bob"}
    assert forge.team_members("@org/missing") is None


def test_local_forge_null_pr(tmp_path):
    assert LocalForge(write(tmp_path, {"pr": None})).pull_request() is None


def test_local_forge_upserts_one_comment(tmp_path):
    path = write(tmp_path, {"pr": None, "comments": ["other"]})
    forge = LocalForge(path)
    forge.upsert_comment(PR, f"{COMMENT_MARKER}\nfirst")
    forge.upsert_comment(PR, f"{COMMENT_MARKER}\nsecond")
    data = json.loads(path.read_text())
    assert data["comments"] == ["other", f"{COMMENT_MARKER}\nsecond"]
    assert path.read_text() == json.dumps(data, indent=2, sort_keys=True) + "\n"


def test_auto_is_none_outside_actions(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert forge_from_option("auto", tmp_path) is None


def test_auto_is_github_on_pull_request_event(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 1}}))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert forge_from_option("auto", tmp_path).name == "github"
    event.write_text("{}")
    assert forge_from_option("auto", tmp_path) is None


@pytest.mark.parametrize("event", [None, "missing", "{not json", "[1]"])
def test_auto_in_actions_with_corrupt_event_raises(tmp_path, monkeypatch, event):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    if event is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        path = tmp_path / "event.json"
        if event != "missing":
            path.write_text(event)
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    with pytest.raises(ForgeError):
        forge_from_option("auto", tmp_path)


def test_options(tmp_path):
    assert forge_from_option("none", tmp_path) is None
    write(tmp_path, {"pr": None})
    assert forge_from_option("local:forge.json", tmp_path).name == "local"
    with pytest.raises(ValueError):
        forge_from_option("gitlab", tmp_path)


def test_local_missing_file_is_forge_error(tmp_path):
    with pytest.raises(ForgeError):
        forge_from_option("local:nope.json", tmp_path).pull_request()


@pytest.mark.parametrize("data, call", [
    ({"reviews": [{"login": "bob"}]}, "reviews"),
    ({"reviews": [3]}, "reviews"),
    ({"reviews": {"a": 1}}, "reviews"),
    ({"reviews": [{"login": "bob", "state": 1, "commit_id": "a", "submitted_at": "t"}]}, "reviews"),
    ({"teams": ["@org/x"]}, "teams"),
    ({"teams": {"@org/x": "bob"}}, "teams"),
    ({"teams": {"@org/x": [1]}}, "teams"),
    ({"pr": [1]}, "pr"),
    ({"pr": {"number": "x", "head_sha": "a", "author": "b"}}, "pr"),
])
def test_local_forge_malformed_entries_raise_forge_error(tmp_path, data, call):
    forge = LocalForge(write(tmp_path, data))
    with pytest.raises(ForgeError):
        {"reviews": lambda: forge.reviews(PR), "teams": lambda: forge.team_members("@org/x"),
         "pr": forge.pull_request}[call]()
