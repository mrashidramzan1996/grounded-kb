from helpers import nt, pdf, text_of

from kb.sources.segment import assign_segment_ids, segment


def test_table_rows_are_segments_with_heading(fixtures):
    n = pdf(fixtures)
    row = next(s for s in segment(n) if "Plan: Team" in text_of(s, n.text))
    assert row.heading.endswith("2. Plans") and row.page == 1
    assert text_of(row, n.text).startswith("| Plan: Team") and "\n" not in text_of(row, n.text)


def test_removing_seats_segment(fixtures):
    n = pdf(fixtures)
    s = next(s for s in segment(n) if s.heading.endswith("3.2 Removing seats"))
    assert "No refund is given." in text_of(s, n.text) and s.page == 1


def test_heading_with_paragraphs_and_list_is_one_segment():
    n = nt("# A\n\nLead-in:\n\n- one\n- two\n\nAfter.\n\n# B\n\nb\n")
    a, b = segment(n)
    assert text_of(a, n.text) == "# A\n\nLead-in:\n\n- one\n- two\n\nAfter."
    assert (a.heading, b.heading) == ("A", "B") and text_of(b, n.text) == "# B\n\nb"


def test_nested_headings_are_a_path():
    n = nt("# A\n\na\n\n## B\n\nb\n")
    assert [s.heading for s in segment(n)] == ["A", "A > B"]


def test_hash_is_first_12_hex_of_sha256():
    import hashlib

    n = nt("# A\n\na\n")
    (s,) = segment(n)
    assert s.hash == hashlib.sha256(text_of(s, n.text).encode()).hexdigest()[:12] and len(s.hash) == 12


def test_long_section_splits_at_sentence_nearest_middle():
    sentences = [f"Sentence number {i:02d} is here." for i in range(100)]
    n = nt("# A\n\n" + " ".join(sentences) + "\n")
    a, b = segment(n)
    assert text_of(a, n.text).endswith(".") and text_of(b, n.text).startswith("Sentence")
    assert abs(len(text_of(a, n.text)) - len(text_of(b, n.text))) < 40
    assert a.heading == b.heading == "A"


def test_page_is_where_segment_starts():
    text = "# A\n\na\n\n# B\n\nb\n"
    n = nt(text)
    n = type(n)(text, [(1, 0), (2, text.index("# B"))])
    assert [s.page for s in segment(n)] == [1, 2]


def test_segment_ids_stable_when_heading_inserted():
    old, n = assign_segment_ids([], segment(nt("# A\n\na\n\n# B\n\nb\n")), 1)
    new, n2 = assign_segment_ids(old, segment(nt("# New\n\nx\n\n# A\n\na\n\n# B\n\nb\n")), n)
    assert [(s.heading, s.id) for s in new] == [("New", "seg-03"), ("A", "seg-01"), ("B", "seg-02")] and n2 == 4


def test_segment_numbers_never_reused():
    old, n = assign_segment_ids([], segment(nt("# A\n\na\n\n# B\n\nb\n")), 1)
    new, n2 = assign_segment_ids(old, segment(nt("# A\n\na\n")), n)
    again, n3 = assign_segment_ids(new, segment(nt("# A\n\na\n\n# C\n\nc\n")), n2)
    assert [s.id for s in again] == ["seg-01", "seg-03"] and n3 == 4


def test_renamed_heading_matches_by_hash():
    row = "| H: v |"
    old, n = assign_segment_ids([], segment(nt(f"# A\n\n{row}\n")), 1)
    new, _ = assign_segment_ids(old, segment(nt(f"# Renamed\n\n{row}\n")), n)
    assert [s.id for s in old] == ["seg-01", "seg-02"]  # the bare heading, then the row
    assert [s.id for s in new] == ["seg-03", "seg-02"] and new[1].heading == "Renamed"
