from dataclasses import replace

from helpers import nt

from kb.sources.segment import segment
from kb.sources.sentences import (
    cues,
    is_normative,
    segment_sentences,
    sentence_key,
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
    for seg in segment(n):
        for s in segment_sentences(n.text, seg, "SRC-x"):
            assert n.text[s.start : s.end] == s.text
            assert seg.start <= s.start < s.end <= seg.end and s.start >= prev
            prev = s.end


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


def test_sentence_key_stable_when_neighbours_change():
    _, a = _units("First one. Seats must stay.\n")
    _, b = _units("Different start here now. Seats  must stay.\n")
    assert sentence_key(a[1]) == sentence_key(b[1]) and len(sentence_key(a[1])) == 12
    assert sentence_key(a[0]) != sentence_key(a[1])
    assert is_normative(a[1]) and not is_normative(a[0])
