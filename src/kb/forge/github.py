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


class GithubForge:
    name = "github"

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
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "grounded-kb",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                link = resp.headers.get("Link") or ""
        except urllib.error.HTTPError as e:
            raise _Http(e.code) from None
        except (urllib.error.URLError, OSError) as e:
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

    def _pages(self, url: str) -> list:
        items: list = []
        seen: set[str] = set()
        while url and url not in seen:
            seen.add(url)
            data, link = self._call("GET", url)
            items.extend(data or [])
            m = _NEXT.search(link)
            url = m.group(1) if m else ""
        return items

    def pull_request(self) -> PullRequest | None:
        path = _env("GITHUB_EVENT_PATH")
        _env("GITHUB_TOKEN")
        _env("GITHUB_REPOSITORY")
        try:
            event = json.loads(Path(path).read_text(encoding="utf-8"))
            pr = event["pull_request"]
            return PullRequest(int(pr["number"]), str(pr["head"]["sha"]), str(pr["user"]["login"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def reviews(self, pr: PullRequest) -> list[Review]:
        url = f"{_api_base()}/repos/{_env('GITHUB_REPOSITORY')}/pulls/{pr.number}/reviews?per_page=100"
        return [
            Review((r.get("user") or {}).get("login", ""), r["state"], r.get("commit_id") or "", r.get("submitted_at") or "")
            for r in self._pages(url)
        ]

    def team_members(self, handle: str) -> set[str] | None:
        org, _, slug = handle.lstrip("@").partition("/")
        if not org or not slug:
            return None
        quote = urllib.parse.quote
        url = f"{_api_base()}/orgs/{quote(org, safe='')}/teams/{quote(slug, safe='')}/members?per_page=100"
        try:
            items: list = []
            seen: set[str] = set()
            while url and url not in seen:
                seen.add(url)
                data, link = self._request("GET", url)
                items.extend(data or [])
                m = _NEXT.search(link)
                url = m.group(1) if m else ""
        except _Http as e:
            if e.code in (403, 404):
                return None
            raise ForgeError(f"GET team members failed with HTTP {e.code}") from None
        return {m["login"] for m in items}

    def upsert_comment(self, pr: PullRequest, body: str) -> None:
        base = f"{_api_base()}/repos/{_env('GITHUB_REPOSITORY')}"
        existing = self._pages(f"{base}/issues/{pr.number}/comments")
        for c in existing:
            if COMMENT_MARKER in (c.get("body") or ""):
                self._call("PATCH", f"{base}/issues/comments/{c['id']}", {"body": body})
                return
        self._call("POST", f"{base}/issues/{pr.number}/comments", {"body": body})


class _Http(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code
