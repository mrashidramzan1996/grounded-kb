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
    parse_id,
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
    original: str  # repo-relative POSIX path, never absolute and never containing `..`
    original_sha256: str
    normaliser: str
    trust: str
    visibility: str
    guard: GuardCounts
    next_seg: int

    @field_validator("original")
    @classmethod
    def _contained_relative_path(cls, v: str) -> str:
        parts = v.split("/")
        if not v or v.startswith("/") or "\\" in v or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"original must be a relative POSIX path without '..' (got {v!r})")
        return v


# why a segment has no record: `none: <code>[: free text]`. A reason without one of these codes still parses (0.3 triage
# files have none); the triage gate warns `triage.none_uncoded`, an error for triage drafted in an ingest or redraft range.
NONE_CODES = ("heading", "background", "contact", "boilerplate", "example", "duplicate", "out-of-scope", "other")
_NONE = "none: "


def none_code(value: str) -> str | None:
    """The reason code of a `none: <code>[: free text]` triage value, or None when it starts with no known code."""
    if not value.startswith(_NONE):
        return None
    code = value[len(_NONE) :].partition(":")[0].strip()
    return code if code in NONE_CODES else None


class Triage(_Model):
    segments: dict[str, list[str] | str]

    @field_validator("segments")
    @classmethod
    def _valid_triage_values(cls, v):
        for seg, val in v.items():
            if isinstance(val, str):
                if not val.startswith(_NONE) or not val[len(_NONE) :].strip():
                    raise ValueError(f"{seg}: a string value must be 'none: <code>[: reason]' with a reason (got {val!r})")
                continue
            if not val:
                raise ValueError(f"{seg}: a list value must not be empty; use 'none: <reason>'")
            for item in val:
                try:
                    parse_id(item)
                except ValueError as e:
                    raise ValueError(f"{seg}: {e}") from e
        return v


Record = Requirement | Feature | Decision | Invariant | Component | Term
