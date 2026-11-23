import re
from enum import Enum
from typing import NamedTuple


class RecordType(str, Enum):
    SOURCE = "source"
    REQUIREMENT = "requirement"
    FEATURE = "feature"
    CLAIM = "claim"
    DECISION = "decision"
    INVARIANT = "invariant"
    COMPONENT = "component"
    TERM = "term"


class ParsedId(NamedTuple):
    type: RecordType
    id: str
    segment: str | None


SOURCE_ID = r"SRC-[a-z0-9][a-z0-9-]*"
SEGMENT = r"seg-\d{2,}"
SOURCE_REF = rf"{SOURCE_ID}#{SEGMENT}"
REQ_ID = r"REQ-(?:F|NF)-\d{3,}"
FEAT_ID = r"FEAT-\d{3,}"
CLAIM_ID = rf"{FEAT_ID}\.C\d+"
ADR_ID = r"ADR-\d{4,}"
INV_ID = r"INV-\d{3,}"
COMP_ID = r"COMP-[a-z0-9][a-z0-9-]*"
TERM_ID = r"TERM-[a-z0-9][a-z0-9-]*"

_PATTERNS: list[tuple[RecordType, re.Pattern[str]]] = [
    (RecordType.SOURCE, re.compile(rf"(?P<id>{SOURCE_ID})(?:#(?P<seg>{SEGMENT}))?")),
    (RecordType.REQUIREMENT, re.compile(rf"(?P<id>{REQ_ID})")),
    (RecordType.CLAIM, re.compile(rf"(?P<id>{CLAIM_ID})")),
    (RecordType.FEATURE, re.compile(rf"(?P<id>{FEAT_ID})")),
    (RecordType.DECISION, re.compile(rf"(?P<id>{ADR_ID})")),
    (RecordType.INVARIANT, re.compile(rf"(?P<id>{INV_ID})")),
    (RecordType.COMPONENT, re.compile(rf"(?P<id>{COMP_ID})")),
    (RecordType.TERM, re.compile(rf"(?P<id>{TERM_ID})")),
]


def parse_id(s: str) -> ParsedId:
    for rtype, pat in _PATTERNS:
        m = pat.fullmatch(s)
        if m:
            seg = m.groupdict().get("seg")
            return ParsedId(rtype, m.group("id"), seg)
    raise ValueError(f"not a valid record id: {s!r}")
