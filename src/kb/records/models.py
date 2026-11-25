import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kb.records.ids import (
    ADR_ID,
    CLAIM_ID,
    COMP_ID,
    EXCL_ID,
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


_MARKUP = re.compile(r"[`<>\[\]*#|\r\n]")


class Requirement(_Record):
    id: str = Field(pattern=rf"^{REQ_ID}$")
    statement: str
    evidence: Evidence
    features: list[str] = []
    modules: list[str] = []
    flags: list[Literal["vague", "conflicting", "untestable"]] = []
    modality: Literal["must", "must_not", "should", "may"] | None = None
    actor: str | None = None  # who the rule binds: a TERM id (which must exist) or plain text, at most 60 characters
    category: Literal["performance", "security", "privacy", "availability", "reliability", "usability",
                      "compliance", "compatibility", "maintainability", "portability"] | None = None
    origin: Literal["stated", "implied"] = "stated"
    entities: list[Annotated[str, Field(pattern=rf"^{TERM_ID}$")]] = []  # the glossary terms (entities) it names

    @field_validator("actor")
    @classmethod
    def _plain_actor(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip() or len(v) > 60 or _MARKUP.search(v) or not all(ch.isprintable() for ch in v):
            raise ValueError("actor must be plain text of 1 to 60 characters without markup")
        return v

    @model_validator(mode="after")
    def _category_only_nf(self):
        if self.category is not None and not self.id.startswith("REQ-NF-"):
            raise ValueError("category is only allowed on REQ-NF-* requirements")
        return self

    @model_validator(mode="after")
    def _needs_feature_or_module(self):
        if not self.features and not self.modules:
            raise ValueError("a requirement needs at least one feature or module (A-10)")
        return self


class Exclusion(_Record):
    """A scope exclusion: an "out of scope" statement, quoted like a requirement. It covers the sentences it quotes."""

    id: str = Field(pattern=rf"^{EXCL_ID}$")
    statement: str
    evidence: Evidence
    modules: list[str] = []
    features: list[str] = []


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


class Relation(_Model):
    """How a term (the source) relates to another term, quoted from a passage that names both."""

    type: Literal["owns", "uses", "contains", "produces", "depends_on", "part_of", "is_a", "triggers"]
    target: str = Field(pattern=rf"^{TERM_ID}$")
    quote: Quote


MAX_RELATIONS = 10


class Term(_Record):
    """A glossary term; with `kind` and `evidence` it is a grounded entity of the domain model."""

    id: str = Field(pattern=rf"^{TERM_ID}$")
    name: str
    aliases: list[str] = []
    definition: str
    kind: Literal["actor", "system", "data", "external", "document", "event", "concept"] | None = None
    evidence: Evidence | None = None
    relations: list[Relation] = Field(default=[], max_length=MAX_RELATIONS)


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


Record = Requirement | Exclusion | Feature | Decision | Invariant | Component | Term
# the record types whose evidence.quotes the quote gate checks
QUOTED_TYPES: tuple[type, ...] = (Requirement, Exclusion, Decision, Invariant)
