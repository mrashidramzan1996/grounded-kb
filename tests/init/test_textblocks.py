import pytest

from kb.textblocks import upsert_block


def test_upsert_block_preserves_and_is_idempotent():
    once = upsert_block("# Ours\nkeep me\n", "rules", "R1", "html")
    assert "keep me" in once and upsert_block(once, "rules", "R1", "html") == once


def test_upsert_block_replaces_only_the_block():
    once = upsert_block("top\n", "rules", "old", "hash")
    twice = upsert_block(once + "bottom\n", "rules", "new", "hash")
    assert twice.startswith("top\n") and twice.endswith("bottom\n")
    assert "new" in twice and "old" not in twice and twice.count("kb:begin rules") == 1


def test_upsert_block_styles():
    assert "<!-- kb:begin x -->" in upsert_block("", "x", "c", "html")
    assert "# kb:begin x" in upsert_block("", "x", "c", "hash")


def test_upsert_block_on_empty_text_has_no_leading_blank_line():
    assert upsert_block("", "x", "c", "hash").startswith("# kb:begin x")


def test_upsert_block_adds_newline_before_block():
    assert upsert_block("no newline", "x", "c", "hash").startswith("no newline\n")


def test_upsert_block_refuses_unbalanced_markers():
    with pytest.raises(ValueError):
        upsert_block("<!-- kb:begin x -->\nhalf\n", "x", "c", "html")


def test_remove_block_undoes_upsert():
    import pytest

    from kb.textblocks import remove_block, upsert_block

    for text in ("", "mine\n", "mine\n\nmore\n"):
        assert remove_block(upsert_block(text, "kb", "x", "html"), "kb", "html") == text
    middle = "a\n<!-- kb:begin kb -->\nx\n<!-- kb:end kb -->\nb\n"
    assert remove_block(middle, "kb", "html") == "a\nb\n" and remove_block("a\n", "kb", "html") == "a\n"
    with pytest.raises(ValueError):
        remove_block("<!-- kb:end kb -->\n<!-- kb:begin kb -->\n", "kb", "html")
