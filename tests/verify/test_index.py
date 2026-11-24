import json
from pathlib import Path

from helpers import make_git_repo

from kb.config.load import load_config
from kb.verify.index import INDEX_PATH, build_index

KB_VALID = Path(__file__).parent.parent / "fixtures" / "kb_valid"
TESTS = "tests/test_seats.py"


def index(tmp_path, files):
    root = make_git_repo(KB_VALID, tmp_path, files)
    return root, build_index(root, load_config(root / "kb.config.yaml"))


def test_marker_inside_test_function_links_test(tmp_path):
    _, idx = index(tmp_path, {TESTS: (
        "def test_other():\n    pass\n\n\n"
        "def test_no_refund_on_removal():\n    # r[verify REQ-F-002]\n    assert True\n"
    )})
    assert idx.tests_for("REQ-F-002") == [f"{TESTS}::test_no_refund_on_removal"]
    assert idx.cards[TESTS].markers == (("verify", "REQ-F-002", 6, f"{TESTS}::test_no_refund_on_removal"),)


def test_marker_in_method_uses_double_colon(tmp_path):
    _, idx = index(tmp_path, {TESTS: (
        "class TestSeats:\n    def test_x(self):\n        # r[verify REQ-F-002]\n        assert True\n"
    )})
    assert idx.tests_for("REQ-F-002") == [f"{TESTS}::TestSeats::test_x"]


def test_marker_outside_test_has_no_ref(tmp_path):
    _, idx = index(tmp_path, {
        TESTS: "# r[verify REQ-F-002]\nX = 1\n\n\ndef test_a():\n    pass\n",
        "services/billing/seats.py": "# r[impl REQ-F-002]\ndef remove_seat():\n    pass\n",
    })
    assert idx.tests_for("REQ-F-002") == []
    assert idx.cards[TESTS].markers == (("verify", "REQ-F-002", 1, None),)
    assert idx.impl_files("REQ-F-002") == ["services/billing/seats.py"]
    assert idx.cards["services/billing/seats.py"].modules == ("MOD-billing",)
    assert [s[:2] for s in idx.cards["services/billing/seats.py"].symbols] == [("services/billing/seats.py#remove_seat", "function")]


def test_importers_of_one_hop(tmp_path):
    _, idx = index(tmp_path, {
        "services/billing/seats.py": "def f():\n    pass\n",
        "services/billing/__init__.py": "",
        "services/billing/api.py": "from services.billing.seats import f\n",
        "services/other.py": "import services.billing\n",
        "services/unrelated.py": "import services.billing_extra\n",
    })
    assert idx.importers_of("services/billing/seats.py") == ["services/billing/api.py"]
    assert idx.importers_of("services/billing/__init__.py") == ["services/billing/api.py", "services/other.py"]


def test_to_json_is_deterministic(tmp_path):
    root, a = index(tmp_path, {TESTS: "def test_a():\n    # r[verify REQ-F-002]\n    pass\n"})
    b = build_index(root, load_config(root / "kb.config.yaml"))
    assert a.to_json() == b.to_json()
    assert json.loads(a.to_json())["cards"][TESTS]["markers"]
    assert INDEX_PATH == ".kb/cache/index.json"


def test_cards_skip_kb_and_source_docs(tmp_path):
    _, idx = index(tmp_path, {
        "docs/spec.py": "def f():\n    pass\n",
        "kb/helper.py": "def g():\n    pass\n",
        "docs/notes.md": "r[impl REQ-F-002]\n",
        "src/a.py": "def h():\n    pass\n",
    })
    assert "docs/spec.py" not in idx.cards and "docs/notes.md" not in idx.cards
    assert "kb/helper.py" not in idx.cards
    assert "src/a.py" in idx.cards
    assert idx.impl_files("REQ-F-002") == []


def test_marker_in_file_without_adapter_gets_card(tmp_path):
    _, idx = index(tmp_path, {"web/app.js": "// r[impl REQ-F-002]\n"})
    card = idx.cards["web/app.js"]
    assert card.symbols == () and card.imports == ()
    assert idx.impl_files("REQ-F-002") == ["web/app.js"]


def test_write_cache_importable():
    from kb.cache import write_cache
    from kb.ingest import brief
    assert brief.write_cache is write_cache


def test_marker_above_test_function_links_test(tmp_path):
    _, idx = index(tmp_path, {TESTS: (
        "import pytest\n\n\n"
        "# r[verify REQ-F-002]\ndef test_a():\n    pass\n\n\n"
        "# r[verify REQ-F-003]\n\n@pytest.mark.slow\ndef test_b():\n    pass\n"
    )})
    assert idx.tests_for("REQ-F-002") == [f"{TESTS}::test_a"]
    assert idx.tests_for("REQ-F-003") == [f"{TESTS}::test_b"]


def test_marker_above_non_test_def_has_no_ref(tmp_path):
    _, idx = index(tmp_path, {TESTS: (
        "# r[verify REQ-F-002]\ndef helper():\n    pass\n\n\n"
        "# r[verify REQ-F-003]\nX = 1\n\n\ndef test_c():\n    pass\n"
    )})
    assert idx.tests_for("REQ-F-002") == []
    assert idx.tests_for("REQ-F-003") == []
