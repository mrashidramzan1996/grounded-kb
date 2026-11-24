import json
import os
import tempfile
from pathlib import Path

from kb.forge import ForgeError
from kb.ports.forge import COMMENT_MARKER, PullRequest, Review


class LocalForge:
    """A forge backed by a JSON file, for tests and offline runs."""

    name = "local"

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ForgeError(f"cannot read forge file {self.path.name}: {e}") from e
        if not isinstance(data, dict):
            raise ForgeError(f"forge file {self.path.name} must hold a JSON object")
        return data

    def pull_request(self) -> PullRequest | None:
        pr = self._load().get("pr")
        if pr is None:
            return None
        try:
            return PullRequest(int(pr["number"]), str(pr["head_sha"]), str(pr["author"]))
        except (KeyError, TypeError, ValueError):
            raise ForgeError("forge file pr needs number, head_sha and author") from None

    def reviews(self, pr: PullRequest) -> list[Review]:
        entries = self._load().get("reviews", [])
        fields = ("login", "state", "commit_id", "submitted_at")
        if not isinstance(entries, list) or not all(
            isinstance(r, dict) and all(isinstance(r.get(k), str) for k in fields) for r in entries
        ):
            raise ForgeError("forge file reviews must be a list of {login, state, commit_id, submitted_at} strings")
        return [Review(*(r[k] for k in fields)) for r in entries]

    def team_members(self, handle: str) -> set[str] | None:
        teams = self._load().get("teams", {})
        if not isinstance(teams, dict):
            raise ForgeError("forge file teams must map a team handle to a list of logins")
        members = teams.get(handle)
        if members is None:
            return None
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise ForgeError(f"forge file teams[{handle!r}] must be a list of logins")
        return set(members)

    def upsert_comment(self, pr: PullRequest, body: str) -> None:
        data = self._load()
        comments = list(data.get("comments", []))
        for i, c in enumerate(comments):
            if COMMENT_MARKER in c:
                comments[i] = body
                break
        else:
            comments.append(body)
        data["comments"] = comments
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".forge-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
