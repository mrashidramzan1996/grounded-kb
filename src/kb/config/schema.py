from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Risk = Literal["low", "standard", "high"]
FailOn = Literal["broken", "schema", "decision_guard", "missing_owner_approval", "stale_context"]
WarnOn = Literal["stale", "coverage_drop"]
EvidenceLevel = Literal["E0", "E1", "E2", "E3", "E4", "E5"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Team(_Model):
    git_team: str


class ModuleCfg(_Model):
    id: str = Field(pattern=r"^MOD-[a-z0-9][a-z0-9-]*$")
    title: str | None = None
    paths: list[str] = []  # empty: planned module, paths filled in later (A-9)
    owner: str
    risk: Risk = "standard"


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
