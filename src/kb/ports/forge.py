from dataclasses import dataclass
from typing import Protocol

COMMENT_MARKER = "<!-- kb-verify -->"


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str
    author: str
    base: str | None = None  # the branch the PR targets, when the forge says; its tip holds the approval policy


@dataclass(frozen=True)
class Review:
    login: str
    state: str  # as GitHub spells it: APPROVED, CHANGES_REQUESTED, COMMENTED, ...
    commit_id: str
    submitted_at: str  # ISO 8601


class Forge(Protocol):
    name: str

    def pull_request(self) -> PullRequest | None: ...

    def reviews(self, pr: PullRequest) -> list[Review]: ...

    def team_members(self, handle: str) -> set[str] | None:
        """Logins of "@org/team"; None when the membership cannot be read."""
        ...

    def upsert_comment(self, pr: PullRequest, body: str) -> None:
        """Create or replace the one comment that holds COMMENT_MARKER."""
        ...
