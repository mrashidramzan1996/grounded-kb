import pytest

from kb.code.registry import NoAdapter, UnsafePath, adapter_for, resolve_anchor, resolve_test
from kb.code.python_pack import PythonPack

SRC = b"@decor\ndef remove_seat(s):\n    return 0\nclass Plan:\n    def price(self): ...\n"


def test_python_symbols():
    assert {"s.py#remove_seat", "s.py#Plan", "s.py#Plan.price"} <= {x.anchor for x in PythonPack().symbols("s.py", SRC)}


def test_kinds_and_byte_range_include_decorator():
    syms = {x.anchor: x for x in PythonPack().symbols("s.py", SRC)}
    assert (syms["s.py#remove_seat"].kind, syms["s.py#Plan"].kind, syms["s.py#Plan.price"].kind) == ("function", "class", "method")
    f = syms["s.py#remove_seat"]
    assert SRC[f.start_byte : f.end_byte].startswith(b"@decor")


def test_nested_things_are_not_anchors():
    src = b"def outer():\n    def inner(): ...\nclass A:\n    class B:\n        def m(self): ...\n    def ok(self):\n        def deep(): ...\n"
    anchors = {x.anchor for x in PythonPack().symbols("s.py", src)}
    assert anchors == {"s.py#outer", "s.py#A", "s.py#A.ok"}


def test_decorated_class_and_method():
    src = b"@d\nclass A:\n    @property\n    def p(self): ...\n"
    assert {"s.py#A", "s.py#A.p"} <= {x.anchor for x in PythonPack().symbols("s.py", src)}


def test_test_functions_in_test_files_have_kind_test():
    src = b"def test_a(): ...\ndef helper(): ...\nclass TestX:\n    def test_b(self): ...\n"
    kinds = {x.anchor: x.kind for x in PythonPack().symbols("tests/test_x.py", src)}
    assert kinds["tests/test_x.py#test_a"] == "test"
    assert kinds["tests/test_x.py#helper"] == "function"
    assert kinds["tests/test_x.py#TestX.test_b"] == "test"
    assert PythonPack().symbols("lib.py", b"def test_a(): ...\n")[0].kind == "function"


def test_adapter_for():
    assert adapter_for("a/b.py").name == "python"
    assert adapter_for("web/app.ts") is None


def test_resolve_anchor(fixtures):
    root = fixtures / "kb_valid"
    sym = resolve_anchor(root, "services/billing/seats.py#remove_seat")
    assert sym is not None and sym.kind == "function"
    assert resolve_anchor(root, "services/billing/seats.py#gone") is None
    assert resolve_anchor(root, "services/billing/missing.py#x") is None


def test_resolve_anchor_no_adapter(fixtures):
    with pytest.raises(NoAdapter):
        resolve_anchor(fixtures / "kb_valid", "web/app.ts#main")


def test_resolve_test(fixtures):
    root = fixtures / "kb_valid"
    sym = resolve_test(root, "tests/test_seats.py::test_no_refund_on_removal")
    assert sym is not None and sym.kind == "test"
    assert resolve_test(root, "tests/test_seats.py::nope") is None
    assert resolve_test(root, "tests/test_seats.py") is None


def test_resolve_test_class_form(tmp_path):
    (tmp_path / "test_c.py").write_text("class TestX:\n    def test_b(self): ...\n")
    assert resolve_test(tmp_path, "test_c.py::TestX::test_b") is not None
    assert resolve_test(tmp_path, "test_c.py::TestX::nope") is None


@pytest.mark.parametrize("anchor", ["/etc/passwd#x.py", "../x.py#a", "a/../../x.py#a", "a\\b.py#a", "./a.py#a", "a//b.py#a"])
def test_unsafe_paths_rejected(tmp_path, anchor):
    with pytest.raises(UnsafePath):
        resolve_anchor(tmp_path, anchor)


def test_symlinks_rejected(tmp_path):
    (tmp_path / "real.py").write_text("def f(): ...\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    (tmp_path / "d").mkdir()
    (tmp_path / "dl").symlink_to(tmp_path / "d")
    (tmp_path / "d" / "m.py").write_text("def f(): ...\n")
    with pytest.raises(UnsafePath):
        resolve_anchor(tmp_path, "link.py#f")
    with pytest.raises(UnsafePath):
        resolve_anchor(tmp_path, "dl/m.py#f")
