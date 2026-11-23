from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    id: str
    heading: str
    page: int
    start: int
    end: int
    hash: str
