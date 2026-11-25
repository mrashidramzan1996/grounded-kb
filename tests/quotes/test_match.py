import pytest

from kb.quotes.match import find_quote, fold


@pytest.mark.parametrize(
    "hay,q,found",
    [
        ("No refund is given.", "No refund is given.", True),
        ("the account’s payment details", "the account's payment details", True),
        ("No refund\n   is given.", "No refund is given.", True),
        ("No refund is given.", "no refund is given.", False),
        ("Wait… what", "Wait... what", True),
        ("a – b — c", "a - b - c", True),
        ("He said “hi”", 'He said "hi"', True),
        ("anything", "", False),
    ],
)
def test_find_quote(hay, q, found):
    assert (find_quote(hay, q) is not None) is found


def test_find_quote_returns_original_offsets():
    hay = "Intro.  The account’s   details\nare kept. Outro."
    s, e = find_quote(hay, "The account's details are kept.")
    assert hay[s:e] == "The account’s   details\nare kept."


def test_find_quote_match_at_end_of_text():
    hay = "x … tail"
    s, e = find_quote(hay, "x ... tail")
    assert hay[s:e] == hay


def test_fold_maps_every_index_and_end():
    folded, mapping = fold("a  “b”…")
    assert folded == 'a "b"...'
    assert len(mapping) == len(folded) + 1
    assert mapping[0] == 0 and mapping[-1] == len("a  “b”…")
    assert mapping[1] == 1  # collapsed whitespace maps to the first space


def test_quote_must_align_to_word_and_number_boundaries():
    assert find_quote("Minimum seats: 25", "Minimum seats: 2") is None
    assert find_quote("two seats", "seat") is None
    assert find_quote("Pay within 14 days.", "within 14 days.") is not None
    hay = "a 2 and a 25"
    assert find_quote(hay, "a 2") == (0, 3)
    assert find_quote("a 25 and a 2", "a 2") == (9, 12)  # skips the earlier non-aligned occurrence
