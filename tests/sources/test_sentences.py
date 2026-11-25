import re
from dataclasses import replace

from helpers import nt

from kb.records.repo import KbRepo
from kb.sources.segment import segment
from kb.sources.sentences import (
    cues,
    is_normative,
    segment_sentences,
    sentence_key,
    sentences,
)


def _units(md: str):
    n = nt(md)
    out = []
    for seg in segment(n):
        out.extend(segment_sentences(n.text, replace(seg, id="seg-01"), "SRC-x"))
    return n, out


def test_sentences_cover_segment_text_in_order():
    n = nt("# A\n\nOne must go. Two should stay.\nWrapped line here. Three.\n\n- x must y\n- z\n\n# B\n\nLast one.\n")
    prev = -1
    count = 0
    for seg in segment(n):
        for s in segment_sentences(n.text, seg, "SRC-x"):
            assert n.text[s.start : s.end] == s.text
            assert seg.start <= s.start < s.end <= seg.end and s.start >= prev
            prev = s.end
            count += 1
    assert count == 7


def test_heading_line_is_not_a_sentence():
    _, out = _units("# Heading One\n\nBody text here.\n")
    assert [s.text for s in out] == ["Body text here."]
    assert [s.index for s in out] == [0]


def test_abbreviations_and_decimals_do_not_split():
    _, out = _units("Use e.g. the 5.5 EUR plan in v3.1. Then stop.\n")
    assert [s.text for s in out] == ["Use e.g. the 5.5 EUR plan in v3.1.", "Then stop."]
    _, out = _units("See Fig. 3 and No. 5 for details, i.e. The rest.\n")
    assert len(out) == 1


def test_list_items_and_table_rows_are_units():
    _, out = _units("- a must x\n- b must y\n")
    assert [s.text for s in out] == ["- a must x", "- b must y"]
    _, out = _units("| Plan: Team | 5 seats |\n")
    assert len(out) == 1


def test_cues():
    assert cues("Refunds must be paid within 14 days.") == {"must", "limit", "number"}
    assert cues("Seats cannot be removed.") == {"must_not"}
    assert cues("Users may export data.") == {"may"}
    assert cues("Users may not export data.") == {"must_not"}
    assert cues("The product helps teams.") == set()
    assert cues("It is no longer offered.") == {"must_not"}
    assert "limit" in cues("Up to 3 seats.")
    assert "number" in cues("Save 50% now.")


def test_number_cue_needs_a_unit_currency_or_percentage():
    assert cues("Refunds are paid within 14 days.") == {"limit", "number"}
    for text in ("A seat costs 5 EUR.", "A seat costs $8.", "It costs €12 a month.", "Pay USD 20 up front.",
                 "Logs are kept 30 days.", "A request takes 200 ms.", "Uploads are capped at 10 MB.",
                 "Teams start with 3 seats.", "Retry 3 times.", "It runs 2 per day.", "A 15% discount applies."):
        assert "number" in cues(text), text
    for text in ("Contact [EMAIL-1] on 2026-01-05.", "Key [SECRET:aws] was rotated.", "Released in 2026.",
                 "The call starts at 10:30.", "See section 3.", "See page 2.", "3.2 Removing seats",
                 "Decisions from the review on 10 November 2026."):
        assert cues(text) == set(), text


def test_sentence_key_stable_when_neighbours_change():
    _, a = _units("First one. Seats must stay.\n")
    _, b = _units("Different start here now. Seats  must stay.\n")
    assert sentence_key(a[1]) == sentence_key(b[1]) and len(sentence_key(a[1])) == 12
    assert sentence_key(a[0]) != sentence_key(a[1])
    assert is_normative(a[1]) and not is_normative(a[0])


def test_sentence_key_ignores_list_markers_and_whitespace():
    _, a = _units("1. X\n")
    _, b = _units("2. X\n")
    assert sentence_key(a[0]) == sentence_key(b[0])
    _, c = _units("- X\n")
    assert sentence_key(c[0]) == sentence_key(a[0])
    _, d = _units("Foo.\n")
    _, e = _units("Foo. \n")
    assert sentence_key(d[0]) == sentence_key(e[0])


def test_trailing_whitespace_and_cr_are_not_in_text():
    n, out = _units("Foo. \r\nBar baz.  \r\n")
    assert [s.text for s in out] == ["Foo.", "Bar baz."]
    assert all(n.text[s.start : s.end] == s.text for s in out)


def test_sentences_on_stored_source(md_repo_ingested):
    repo = KbRepo.load(md_repo_ingested)
    src_id = next(iter(repo.sources))
    out = sentences(repo, src_id)
    text = repo.source_text(src_id)
    assert out
    assert all(text[s.start : s.end] == s.text for s in out)
    assert all(re.fullmatch(rf"{re.escape(src_id)}#seg-\d+", s.ref) for s in out)
    assert [(s.start) for s in out] == sorted(s.start for s in out)
    for ref in {s.ref for s in out}:
        assert [s.index for s in out if s.ref == ref] == list(range(len([1 for s in out if s.ref == ref])))


def test_table_separator_rows_are_not_units():
    _, out = _units("| A | B |\n|---|---|\n| 1 | 2 |\n")
    assert len(out) == 2
    _, out = _units("| A | B |\n| --- | :-: |\n| 1 | 2 |\n")
    assert len(out) == 2


def test_indented_list_items_are_units():
    _, out = _units("- a\n  - nested\n   1. x\n")
    assert len(out) == 3


def test_fenced_code_is_not_units():
    _, out = _units("Before text.\n\n```\ncode line one.\n- not a unit\n```\n\nAfter text.\n")
    assert [s.text for s in out] == ["Before text.", "After text."]
    _, out = _units("~~~\nx\n~~~\nAfter.\n")
    assert [s.text for s in out] == ["After."]


def test_crlf_line_endings():
    _, out = _units("- a must x\r\n- b must y\r\n")
    assert [s.text for s in out] == ["- a must x", "- b must y"]


def test_abbreviation_with_leading_punctuation_and_decimal():
    _, out = _units("Use it (e.g. This) Then stop.\n")
    assert len(out) == 1
    _, out = _units("It costs 5.5 EUR. Next one.\n")
    assert [s.text for s in out] == ["It costs 5.5 EUR.", "Next one."]
