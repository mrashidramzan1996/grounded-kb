import http.server
import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from kb.forge import ForgeError, github
from kb.forge.github import GithubForge
from kb.ports.forge import COMMENT_MARKER, PullRequest

API = "https://api.github.com"
TOKEN = "ghs_secrettoken123"
PR = PullRequest(5, "b" * 40, "alice")


class Resp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 5, "head": {"sha": "b" * 40}, "user": {"login": "alice"}}}))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_API_URL", raising=False)


def fake(monkeypatch, routes):
    calls = []

    def urlopen(req, timeout=None):  # replaces the opener
        calls.append(req)
        key = (req.get_method(), req.full_url)
        if key not in routes:
            raise AssertionError(f"unexpected {key}")
        out = routes[key]
        if isinstance(out, int):
            raise urllib.error.HTTPError(req.full_url, out, "x", {}, io.BytesIO(b"{}"))
        return out

    monkeypatch.setattr(github, "_open", urlopen)
    return calls


def test_pull_request_from_event(env):
    assert GithubForge().pull_request() == PR


def test_github_paginates_reviews_and_maps_404_to_none(env, monkeypatch):
    p1 = f"{API}/repos/org/repo/pulls/5/reviews?per_page=100"
    p2 = f"{API}/repos/org/repo/pulls/5/reviews?per_page=100&page=2"
    r = lambda login: {"user": {"login": login}, "state": "APPROVED", "commit_id": "b" * 40, "submitted_at": "t"}
    calls = fake(monkeypatch, {
        ("GET", p1): Resp([r("bob")], {"Link": f'<{p2}>; rel="next", <{p2}>; rel="last"'}),
        ("GET", p2): Resp([r("carol")]),
        ("GET", f"{API}/orgs/org/teams/gone/members?per_page=100"): 404,
        ("GET", f"{API}/orgs/org/teams/priv/members?per_page=100"): 403,
        ("GET", f"{API}/orgs/org/teams/dev/members?per_page=100"): Resp([{"login": "bob"}]),
    })
    forge = GithubForge()
    assert [x.login for x in forge.reviews(PR)] == ["bob", "carol"]
    assert forge.team_members("@org/gone") is None
    assert forge.team_members("@org/priv") is None
    assert forge.team_members("@org/dev") == {"bob"}
    h = {k.lower(): v for k, v in calls[0].header_items()}
    assert h["authorization"] == f"Bearer {TOKEN}"
    assert h["accept"] == "application/vnd.github+json"
    assert h["x-github-api-version"] == "2022-11-28"
    assert h["user-agent"] == "grounded-kb"


def test_upsert_comment_patches_existing_or_posts(env, monkeypatch):
    url = f"{API}/repos/org/repo/issues/5/comments"
    calls = fake(monkeypatch, {
        ("GET", url + "?per_page=100"): Resp([
            {"id": 11, "body": "x", "user": {"type": "Bot"}},
            {"id": 12, "body": f"{COMMENT_MARKER}\nold", "user": {"type": "Bot"}},
        ]),
        ("PATCH", f"{API}/repos/org/repo/issues/comments/12"): Resp({}),
    })
    GithubForge().upsert_comment(PR, f"{COMMENT_MARKER}\nnew")
    assert json.loads(calls[-1].data) == {"body": f"{COMMENT_MARKER}\nnew"}

    calls = fake(monkeypatch, {("GET", url + "?per_page=100"): Resp([]), ("POST", url): Resp({})})
    GithubForge().upsert_comment(PR, "b")
    assert calls[-1].get_method() == "POST"


def test_other_http_error_is_forge_error_without_token(env, monkeypatch):
    fake(monkeypatch, {("GET", f"{API}/repos/org/repo/pulls/5/reviews?per_page=100"): 500})
    with pytest.raises(ForgeError) as e:
        GithubForge().reviews(PR)
    assert TOKEN not in str(e.value)


@pytest.mark.parametrize("var", ["GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH"])
def test_missing_env_names_variable(env, monkeypatch, var):
    monkeypatch.delenv(var)
    with pytest.raises(ForgeError, match=var):
        GithubForge().pull_request()


def test_refuses_insecure_api_base(env, monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "http://evil.example.com")
    with pytest.raises(ForgeError) as e:
        GithubForge().reviews(PR)
    assert TOKEN not in str(e.value)


def test_allows_localhost_http(env, monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "http://127.0.0.1:9")
    fake(monkeypatch, {("GET", "http://127.0.0.1:9/orgs/o/teams/t/members?per_page=100"): Resp([])})
    assert GithubForge().team_members("@o/t") == set()


def test_marker_in_human_comment_is_not_overwritten(env, monkeypatch):
    url = f"{API}/repos/org/repo/issues/5/comments"
    calls = fake(monkeypatch, {
        ("GET", url + "?per_page=100"): Resp([{"id": 9, "body": f"{COMMENT_MARKER} spoof", "user": {"type": "User"}}]),
        ("POST", url): Resp({}),
    })
    GithubForge().upsert_comment(PR, f"{COMMENT_MARKER}\nnew")
    assert [c.get_method() for c in calls] == ["GET", "POST"]


def test_corrupt_event_file_raises(env, tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text("{not json")
    with pytest.raises(ForgeError):
        GithubForge().pull_request()
    event.write_text(json.dumps({"pull_request": {"number": 1}}))
    with pytest.raises(ForgeError):
        GithubForge().pull_request()
    event.write_text("{}")
    assert GithubForge().pull_request() is None
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(ForgeError):
        GithubForge().pull_request()


@pytest.mark.parametrize("body", [{"a": 1}, [{"state": "APPROVED"}, 3], [{"nostate": 1}]])
def test_malformed_reviews_raise_forge_error(env, monkeypatch, body):
    fake(monkeypatch, {("GET", f"{API}/repos/org/repo/pulls/5/reviews?per_page=100"): Resp(body)})
    with pytest.raises(ForgeError):
        GithubForge().reviews(PR)


def test_redirect_never_forwards_token(env, monkeypatch):
    seen: list[dict] = []

    class Sink(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *a):
            pass

    sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)
    target = f"http://127.0.0.1:{sink.server_port}/stolen"

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *a):
            pass

    front = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    for srv in (sink, front):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{front.server_port}")
    try:
        with pytest.raises(ForgeError, match="unexpected redirect") as e:
            GithubForge().reviews(PR)
    finally:
        front.shutdown()
        sink.shutdown()
    assert TOKEN not in str(e.value)
    assert seen == []
