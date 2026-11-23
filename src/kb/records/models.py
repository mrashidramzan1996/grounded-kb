from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kb.records.ids import (
    ADR_ID,
    CLAIM_ID,
    COMP_ID,
    FEAT_ID,
    INV_ID,
    REQ_ID,
    SOURCE_ID,
    SOURCE_REF,
    TERM_ID,
)


class _Model(BaseModel):
    # extra="forbid" is what rejects derived fields (A-1/A-2) in record files.
    model_config = ConfigDict(extra="forbid")


class _Record(_Model):
    schema_version: Literal[1] = 1
    status: Literal["live", "rejected"] = "live"
    owner: str | None = None
    visibility: str = "internal"
    drafted_by: str | None = None


class Quote(_Model):
    source: str = Field(pattern=rf"^{SOURCE_REF}$")
    text: str


class Evidence(_Model):
    mode: Literal["all", "any"] = "all"
    quotes: list[Quote] = Field(min_length=1)


class Requirement(_Record):
    id: str = Field(pattern=rf"^{REQ_ID}$")
    statement: str
    evidence: Evidence
    features: list[str] = []
    modules: list[str] = []
    flags: list[Literal["vague", "conflicting", "untestable"]] = []

    @model_validator(mode="after")
    def _needs_feature_or_module(self):
        if not self.features and not self.modules:
            raise ValueError("a requirement needs at least one feature or module (A-10)")
        return self


class FeatureClaim(_Model):
    id: str = Field(pattern=rf"^{CLAIM_ID}$")
    claim: str
    requirement: str | None = None
    code: str | None = None
    test: str | None = None


class Feature(_Record):
    id: str = Field(pattern=rf"^{FEAT_ID}$")
    title: str
    module: str
    requirements: list[str] = []
    claims: list[FeatureClaim] = []
    body: str = ""


class Decision(_Record):
    id: str = Field(pattern=rf"^{ADR_ID}$")
    title: str
    supersedes: str | None = None
    governs: list[str] = Field(min_length=1)
    evidence: Evidence | None = None
    decided_by: str | None = None
    revisit_when: str | None = None
    body: str


class Invariant(_Record):
    id: str = Field(pattern=rf"^{INV_ID}$")
    statement: str
    governs: list[str] = Field(min_length=1)
    evidence: Evidence | None = None
    tests: list[str] = []
    supersedes: str | None = None
    body: str = ""


class Component(_Record):
    id: str = Field(pattern=rf"^{COMP_ID}$")
    title: str
    module: str
    body: str = ""


class Term(_Record):
    id: str = Field(pattern=rf"^{TERM_ID}$")
    name: str
    aliases: list[str] = []
    definition: str


class GuardCounts(_Model):
    emails: int
    secrets: int
    injections: int
    injection_segments: list[str] = []


class Source(_Model):
    id: str = Field(pattern=rf"^{SOURCE_ID}$")
    original: str
    original_sha256: str
    normaliser: str
    trust: str
    visibility: str
    guard: GuardCounts
    next_seg: int


class Triage(_Model):
    segments: dict[str, list[str] | str]

    @field_validator("segments")
    @classmethod
    def _none_needs_reason(cls, v):
        for seg, val in v.items():
            if isinstance(val, str) and not val.startswith("none: "):
                raise ValueError(f"{seg}: a string value must start with 'none: ' (got {val!r})")
        return v


Record = Requirement | Feature | Decision | Invariant | Component | Term
