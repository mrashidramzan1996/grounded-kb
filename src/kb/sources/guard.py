"""Mask secrets and emails, flag prompt injection (spec §6.3).

The guard runs on normalised text before anything is stored, so nothing unmasked reaches kb/.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from kb.records.models import GuardCounts

_SECRETS = [
    ("aws-access-key", r"AKIA[0-9A-Z]{16}"),
    ("stripe-test-key", r"sk_test_[0-9A-Za-z]{16,}"),
    ("stripe-live-key", r"sk_live_[0-9A-Za-z]{16,}"),
    ("github-token", r"(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{22,})"),
]
_EMAIL_LOCAL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
_EMAIL_DOMAIN = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")
EMAIL_SIDE_CAP = 512  # characters taken either side of an '@'; a longer token is masked up to the cap
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ ){0,3}PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END (?:[A-Z]+ ){0,3}PRIVATE KEY-----")
_SECRET = re.compile("|".join(f"(?P<s{i}>{rx})" for i, (_, rx) in enumerate(_SECRETS)))
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


def _private_keys(text: str) -> list[tuple[int, int]]:
    """Spans from each BEGIN marker to its END marker (to the end of the text when there is none)."""
    spans, pos = [], 0
    while begin := _PRIVATE_KEY.search(text, pos):
        end = _PRIVATE_KEY_END.search(text, begin.end())
        if end is None:  # no END anywhere after this BEGIN, so none after any later one either
            spans.append((begin.start(), len(text)))
            break
        spans.append((begin.start(), end.end()))
        pos = end.end()
    return spans


def _email_spans(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Tokens around each '@' in text[lo:hi]: linear, and over-long addresses are cut at the cap, never skipped."""
    spans, last, at = [], lo, text.find("@", lo, hi)
    while at != -1:
        left = at
        while left > last and at - left < EMAIL_SIDE_CAP and text[left - 1] in _EMAIL_LOCAL:
            left -= 1
        right = at + 1
        while right < hi and right - at <= EMAIL_SIDE_CAP and text[right] in _EMAIL_DOMAIN:
            right += 1
        domain = text[at + 1 : right].rstrip(".-")
        if left < at and any(domain.split(".")[1:]):
            spans.append((left, at + 1 + len(domain)))
            last = spans[-1][1]
        at = text.find("@", max(at + 1, last), hi)
    return spans


def guard(text: str) -> GuardResult:
    keys = _private_keys(text)
    found: list[tuple[int, int, str | None]] = [(s, e, "private-key") for s, e in keys]
    gaps, pos = [], 0
    for s, e in keys:
        gaps.append((pos, s))
        pos = e
    gaps.append((pos, len(text)))
    for lo, hi in gaps:  # each gap is scanned on its own, so a match is cut at a key block's edge
        cands: list[tuple[int, int, str | None]] = []
        for m in _SECRET.finditer(text, lo, hi):
            cands.append((m.start(), m.end(), next(k for i, (k, _) in enumerate(_SECRETS) if m.group(f"s{i}"))))
        cands += [(s, e, None) for s, e in _email_spans(text, lo, hi)]
        cands.sort(key=lambda c: (c[0], c[2] is None))
        merged: list[list] = []
        for s, e, kind in cands:
            if merged and s < merged[-1][1]:  # overlapping matches become one mask
                merged[-1][1] = max(merged[-1][1], e)
                merged[-1][2] = merged[-1][2] or kind
            else:
                merged.append([s, e, kind])
        found += [tuple(c) for c in merged]  # type: ignore[misc]
    found.sort(key=lambda f: f[0])

    out: list[str] = []
    edits: list[tuple[int, int, int]] = []
    kinds: set[str] = set()
    emails: dict[str, int] = {}
    email_count = secret_count = last = 0
    for start, end, kind in found:
        if kind is None:
            n = emails.setdefault(text[start:end].lower(), len(emails) + 1)
            mask = f"[EMAIL-{n}]"
            email_count += 1
        else:
            mask = f"[SECRET:{kind}]"
            kinds.add(kind)
            secret_count += 1
        out.append(text[last:start])
        out.append(mask)
        edits.append((start, end, len(mask)))
        last = end
    out.append(text[last:])
    masked = "".join(out)
    spans = [m.span() for m in _INJECTION.finditer(masked)]
    counts = GuardCounts(emails=email_count, secrets=secret_count, injections=len(spans))
    return GuardResult(masked, counts, sorted(kinds), spans, edits)
