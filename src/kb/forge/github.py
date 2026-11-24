import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from kb.forge import ForgeError
from kb.ports.forge import COMMENT_MARKER, PullRequest, Review

DEFAULT_API = "https://api.github.com"
TIMEOUT = 30
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ForgeError(f"{name} is not set")
    return value


def _api_base() -> str:
    base = os.environ.get("GITHUB_API_URL", DEFAULT_API).rstrip("/")
    parts = urllib.parse.urlsplit(base)
    local_http = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not local_http:
        raise ForgeError("GITHUB_API_URL must be https (http is allowed only for localhost)")
    return base


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The REST endpoints used here never redirect; following one could carry credentials elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ForgeError(f"unexpected redirect from {req.full_url}")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout):
    return _OPENER.open(req, timeout=timeout)


class GithubForge:
    name = "github"

    def __init__(self) -> None:
        self._pr: tuple[PullRequest | None] | None = None  # the event's pull request, read once

    def _request(self, method: str, url: str, payload: dict | None = None):
        token = _env("GITHUB_TOKEN")
        base = _api_base()
        if url != base and not url.startswith(base + "/"):
            raise ForgeError("refusing to send credentials outside the GitHub API base")
        req = urllib.request.Request(
            url,
            method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "grounded-kb",
            },
        )
        req.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with _open(req, TIMEOUT) as resp:
                body = resp.read()
                link = resp.headers.get("Link") or ""
        except urllib.error.HTTPError as e:
            raise _Http(e.code) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise ForgeError(f"{method} {url} failed: {type(e).__name__}") from None
        try:
            return (json.loads(body) if body else None), link
        except ValueError:
            raise ForgeError(f"{method} {url} returned invalid JSON") from None

    def _call(self, method: str, url: str, payload: dict | None = None):
        try:
            return self._request(method, url, payload)
        except _Http as e:
            raise ForgeError(f"{method} {url} failed with HTTP {e.code}") from None

    def _pages(self, url: str, call=None) -> list:
        call = call or self._call
        items: list = []
        seen: set[str] = set()
        while url and url not in seen:
            seen.add(url)
            data, link = call("GET", url)
            if data is None:
                data = []
            if not isinstance(data, list):
                raise ForgeError("GitHub returned an unexpected response (expected a list)")
            items.extend(data)
            m = _NEXT.search(link)
            url = m.group(1) if m else ""
        return items

    def pull_request(self) -> PullRequest | None:
        """The event's pull request, snapshotted on the first call: a test run that rewrites the event file
        afterwards cannot change which PR the approvals are checked for."""
        if self._pr is None:
            self._pr = (self._read_event(),)
        return self._pr[0]

    def _read_event(self) -> PullRequest | None:
        path = _env("GITHUB_EVENT_PATH")
        _env("GITHUB_TOKEN")
        _env("GITHUB_REPOSITORY")
        try:
            event = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ForgeError(f"cannot read the event file: {type(e).__name__}") from None
        if not isinstance(event, dict):
            raise ForgeError("the event file must hold a JSON object")
        if "pull_request" not in event:
            return None
        try:
            pr = event["pull_request"]
            return PullRequest(int(pr["number"]), str(pr["head"]["sha"]), str(pr["user"]["login"]))
        except (KeyError, TypeError, ValueError):
            raise ForgeError("the event's pull_request lacks number, head.sha or user.login") from None

    def reviews(self, pr: PullRequest) -> list[Review]:
        url = f"{_api_base()}/repos/{_env('GITHUB_REPOSITORY')}/pulls/{pr.number}/reviews?per_page=100"
        try:
            return [
                Review((r.get("user") or {}).get("login", ""), r["state"], r.get("commit_id") or "", r.get("submitted_at") or "")
                for r in self._pages(url)
            ]
        except (KeyError, TypeError, AttributeError):
            raise ForgeError("GitHub returned malformed reviews") from None

    def team_members(self, handle: str) -> set[str] | None:
        org, _, slug = handle.lstrip("@").partition("/")
        if not org or not slug:
            return None
        quote = urllib.parse.quote
        url = f"{_api_base()}/orgs/{quote(org, safe='')}/teams/{quote(slug, safe='')}/members?per_page=100"
        try:
            items = self._pages(url, self._request)
        except _Http as e:
            if e.code in (403, 404):
                return None
            raise ForgeError(f"GET team members failed with HTTP {e.code}") from None
        try:
            return {m["login"] for m in items}
        except (KeyError, TypeError):
            raise ForgeError("GitHub returned malformed team members") from None

    def upsert_comment(self, pr: PullRequest, body: str) -> None:
        base = f"{_api_base()}/repos/{_env('GITHUB_REPOSITORY')}"
        existing = self._pages(f"{base}/issues/{pr.number}/comments?per_page=100")
        try:
            # only our own (bot) comment is updated; a human quoting the marker is never overwritten
            mine = [c for c in existing if COMMENT_MARKER in (c.get("body") or "") and (c.get("user") or {}).get("type") == "Bot"]
            target = mine[0]["id"] if mine else None
        except (KeyError, TypeError, AttributeError):
            raise ForgeError("GitHub returned malformed comments") from None
        if target is not None:
            self._call("PATCH", f"{base}/issues/comments/{target}", {"body": body})
            return
        self._call("POST", f"{base}/issues/{pr.number}/comments", {"body": body})


class _Http(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code
