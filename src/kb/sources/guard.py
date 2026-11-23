"""Mask secrets and emails, flag prompt injection (spec §6.3).

The guard runs on normalised text before anything is stored, so nothing unmasked reaches kb/.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from kb.records.models import GuardCounts

_SECRETS = [
    ("private-key", r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----(?:.*?-----END (?:[A-Z]+ )*PRIVATE KEY-----|.*\Z)"),
    ("aws-access-key", r"AKIA[0-9A-Z]{16}"),
    ("stripe-test-key", r"sk_test_[0-9A-Za-z]{16,}"),
    ("stripe-live-key", r"sk_live_[0-9A-Za-z]{16,}"),
    ("github-token", r"(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,})"),
]
_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
_MASK = re.compile(
    "|".join([f"(?P<s{i}>{rx})" for i, (_, rx) in enumerate(_SECRETS)] + [f"(?P<email>{_EMAIL})"]), re.DOTALL
)
_INJECTION = re.compile(
    r"ignore (?:all|any|previous|your) instructions|disregard (?:the|all|previous)|you are now|system prompt",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardResult:
    text: str
    counts: GuardCounts
    secret_kinds: list[str]
    injection_spans: list[tuple[int, int]] = field(default_factory=list)  # offsets in the masked text
    edits: list[tuple[int, int, int]] = field(default_factory=list)  # (original start, end, masked length)

    def map_offset(self, offset: int) -> int:
        """Where an offset of the original text lies in the masked text (inside a mask: its start)."""
        i = bisect_right([e[0] for e in self.edits], offset)
        before = sum(m - (e - s) for s, e, m in self.edits[: i - 1 if self._inside(i, offset) else i])
        if self._inside(i, offset):
            return self.edits[i - 1][0] + before
        return offset + before

    def _inside(self, i: int, offset: int) -> bool:
        return i > 0 and offset < self.edits[i - 1][1]


def guard(text: str) -> GuardResult:
    out: list[str] = []
    edits: list[tuple[int, int, int]] = []
    kinds: set[str] = set()
    emails: dict[str, int] = {}
    email_count = secret_count = 0
    last = 0
    for m in _MASK.finditer(text):
        if m.group("email"):
            n = emails.setdefault(m.group().lower(), len(emails) + 1)
            mask = f"[EMAIL-{n}]"
            email_count += 1
        else:
            kind = next(k for i, (k, _) in enumerate(_SECRETS) if m.group(f"s{i}"))
            mask = f"[SECRET:{kind}]"
            kinds.add(kind)
            secret_count += 1
        out.append(text[last : m.start()])
        out.append(mask)
        edits.append((m.start(), m.end(), len(mask)))
        last = m.end()
    out.append(text[last:])
    masked = "".join(out)
    spans = [m.span() for m in _INJECTION.finditer(masked)]
    counts = GuardCounts(emails=email_count, secrets=secret_count, injections=len(spans))
    return GuardResult(masked, counts, sorted(kinds), spans, edits)
