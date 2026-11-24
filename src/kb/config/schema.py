import shlex
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Risk = Literal["low", "standard", "high"]
FailOn = Literal["broken", "schema", "decision_guard", "missing_owner_approval", "stale_context"]
WarnOn = Literal["stale", "coverage_drop"]
EvidenceLevel = Literal["E0", "E1", "E2", "E3", "E4", "E5"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Team(_Model):
    git_team: str


_CATCH_ALL = {"**", "*", "**/*", ".", "./**"}


class TestsCfg(_Model):
    __test__ = False  # not a pytest class, despite the name

    preset: Literal["pytest"] | None = None
    run: str | None = None  # e.g. "pytest {tests} --junitxml={junit}"; needs both tokens
    id_format: str = "{path}::{name}"  # {path} file, {name} "Class::func" or "func"
    timeout_seconds: int = Field(900, ge=1)

    @model_validator(mode="after")
    def _one_runner(self) -> "TestsCfg":
        if (self.preset is None) == (self.run is None):
            raise ValueError("tests needs exactly one of preset or run")
        if self.run is not None:
            try:
                tokens = shlex.split(self.run)
            except ValueError as e:
                raise ValueError(f"tests.run is not a valid command: {e}") from e
            if "{tests}" not in tokens:
                raise ValueError("tests.run needs {tests} as a separate argument (not part of another word, e.g. --ids={tests})")
            if "{junit}" not in self.run:
                raise ValueError("tests.run must contain {junit}")
        return self


class ModuleCfg(_Model):
    id: str = Field(pattern=r"^MOD-[a-z0-9][a-z0-9-]*$")
    title: str | None = None
    paths: list[str] = []  # empty: planned module, paths filled in later (A-9)
    owner: str
    risk: Risk = "standard"
    tests: TestsCfg | None = None

    @field_validator("paths")
    @classmethod
    def _safe_globs(cls, paths: list[str]) -> list[str]:
        for g in paths:
            if not g.strip():
                raise ValueError("module path globs must not be empty")
            if g.startswith("/") or "\\" in g:
                raise ValueError(f"module path glob {g!r} must be relative and use '/' separators")
            if ".." in g.split("/"):
                raise ValueError(f"module path glob {g!r} must not contain '..'")
            if g in _CATCH_ALL:
                raise ValueError(f"module path glob {g!r} would match every file; name a directory")
        return paths


class SourceCfg(_Model):
    paths: list[str]
    trust: Literal["spec", "design", "discussion", "external"] = "spec"
    visibility: str = "internal"  # open str: visibility labels are user-defined
    adapter: str | None = None
    mapping: dict[str, Any] | None = None


class RestrictedCfg(_Model):
    paths: list[str]
    readers: list[str]


class VisibilityCfg(_Model):
    restricted: list[RestrictedCfg] = []


class EvidenceCfg(_Model):
    required: dict[Risk, EvidenceLevel] = {"low": "E2", "standard": "E4", "high": "E5"}


class ApproversCfg(_Model):
    intent: list[str] = []
    decisions: list[str] = []


class PolicyCfg(_Model):
    mode: Literal["warn-only", "enforce"] = "warn-only"
    fail_on: list[FailOn] = ["broken", "schema", "decision_guard", "missing_owner_approval", "stale_context"]
    warn_on: list[WarnOn] = ["stale", "coverage_drop"]
    approvers: ApproversCfg = ApproversCfg()


class ReviewCfg(_Model):
    max_items_per_pr: int = 40
    escalate_after_days: int = 5


class CoordinationCfg(_Model):
    lease_minutes: int = 30
    broadcast_rate_per_hour: int = 6
    ask_reply_timeout_minutes: int = 10


class KbConfig(_Model):
    version: Literal[1]
    teams: dict[str, Team] = {}
    modules: list[ModuleCfg] = []
    sources: list[SourceCfg] = []
    visibility: VisibilityCfg = VisibilityCfg()
    models: dict[str, Literal["small", "standard"]] = {}
    evidence: EvidenceCfg = EvidenceCfg()
    policy: PolicyCfg = PolicyCfg()
    review: ReviewCfg = ReviewCfg()
    coordination: CoordinationCfg = CoordinationCfg()
    ignore: list[str] = []
    generated: list[str] = []
    bots: list[str] = ["*[bot]"]
