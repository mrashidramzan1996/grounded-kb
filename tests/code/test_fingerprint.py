import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kb.code.python_pack import PythonPack
from kb.code.registry import NoAdapter, fingerprint_of

SRC = b'def remove_seat(seat, *, refund=False):\n    """Remove."""\n    # note\n    return (charge(seat, \'x\',))\n'
FIXTURE = Path(__file__).parent.parent / "fixtures" / "fingerprint" / "unformatted.py"


def fp(src: bytes, name="remove_seat"):
    pack = PythonPack()
    sym = next(s for s in pack.symbols("billing/seats.py", src) if s.name == name)
    return pack.fingerprint(src, sym)


def test_fingerprint_is_16_hex():
    assert re.fullmatch(r"[0-9a-f]{16}", fp(SRC))


def test_comments_docstring_quotes_trailing_comma_parens_ignored():
    assert fp(SRC) == fp(b'def remove_seat(seat, *, refund=False):\n    return charge(seat, "x")\n')


def test_logic_change_changes_fingerprint():
    assert fp(SRC) != fp(SRC.replace(b"refund=False", b"refund=True"))


def test_string_value_change_changes_fingerprint():
    assert fp(SRC) != fp(SRC.replace(b"'x'", b"'y'"))


def test_call_parentheses_are_kept():
    assert fp(b"def f():\n    return g()\n", "f") != fp(b"def f():\n    return g\n", "f")


def test_decorator_change_changes_fingerprint():
    assert fp(b"@a\ndef f(): return 1\n", "f") != fp(b"@b\ndef f(): return 1\n", "f")


def test_method_fingerprint_independent_of_sibling_methods():
    one = b"class C:\n    def m(self):\n        return 1\n"
    two = one + b"    def other(self):\n        return 2\n"
    assert fp(one, "C.m") == fp(two, "C.m")


def test_method_body_change_changes_method_not_sibling():
    a = b"class C:\n    def m(self):\n        return 1\n    def n(self):\n        return 2\n"
    b = a.replace(b"return 1", b"return 3")
    assert fp(a, "C.m") != fp(b, "C.m")
    assert fp(a, "C.n") == fp(b, "C.n")


def test_fstring_quote_style_ignored_but_content_not():
    assert fp(b"def f(x):\n    return f'{x}!'\n", "f") == fp(b'def f(x):\n    return f"{x}!"\n', "f")
    assert fp(b"def f(x):\n    return f'{x}!'\n", "f") != fp(b"def f(x):\n    return f'{x}?'\n", "f")


def test_ruff_format_changes_no_fingerprint(tmp_path):
    before = tmp_path / "before.py"
    after = tmp_path / "after.py"
    shutil.copy(FIXTURE, before)
    shutil.copy(FIXTURE, after)
    subprocess.run(["uv", "run", "ruff", "format", str(after)], check=True, capture_output=True)
    assert after.read_bytes() != before.read_bytes()
    pack = PythonPack()
    b_src, a_src = before.read_bytes(), after.read_bytes()
    b_syms = pack.symbols("m.py", b_src)
    a_syms = pack.symbols("m.py", a_src)
    assert len(b_syms) >= 5
    assert [s.anchor for s in b_syms] == [s.anchor for s in a_syms]
    for bs, as_ in zip(b_syms, a_syms):
        assert pack.fingerprint(b_src, bs) == pack.fingerprint(a_src, as_), bs.anchor


def test_fingerprint_of_resolves_anchor(tmp_path):
    (tmp_path / "a.py").write_bytes(SRC.replace(b"remove_seat", b"f"))
    (tmp_path / "x.rb").write_text("def f; end\n")
    got = fingerprint_of(tmp_path, "a.py#f")
    assert got and re.fullmatch(r"[0-9a-f]{16}", got)
    assert fingerprint_of(tmp_path, "a.py#missing") is None
    assert fingerprint_of(tmp_path, "gone.py#f") is None
    with pytest.raises(NoAdapter):
        fingerprint_of(tmp_path, "x.rb#f")


def test_fingerprint_of_source_replaces_disk(tmp_path):
    (tmp_path / "a.py").write_bytes(b"def f():\n    return 1\n")
    old = b"def f():\n    return 2\n"
    (other := tmp_path / "other").mkdir()
    (other / "a.py").write_bytes(old)  # the same bytes on disk give the same fingerprint as source=
    assert fingerprint_of(tmp_path, "a.py#f", source=old) == fingerprint_of(other, "a.py#f")
    assert fingerprint_of(tmp_path, "a.py#f", source=old) != fingerprint_of(tmp_path, "a.py#f")
    assert fingerprint_of(tmp_path, "gone.py#f", source=old) is not None
    assert fingerprint_of(tmp_path, "a.py#g", source=old) is None


def test_parentheses_that_change_precedence_change_fingerprint():
    assert fp(b"def f(a, b, c):\n    return (a + b) * c\n", "f") != fp(b"def f(a, b, c):\n    return a + (b * c)\n", "f")
    assert fp(b"def f(a, b):\n    return not (a and b)\n", "f") != fp(b"def f(a, b):\n    return not a and b\n", "f")
    assert fp(b"def f(a, b):\n    return (a + b).x\n", "f") != fp(b"def f(a, b):\n    return a + b.x\n", "f")


def test_single_element_tuple_comma_kept():
    assert fp(b"def f(a):\n    return (a,)\n", "f") != fp(b"def f(a):\n    return (a)\n", "f")
    assert fp(b"def f(a):\n    return (a,)\n", "f") == fp(b"def f(a):\n    return (a, )\n", "f")
