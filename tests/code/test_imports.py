from kb.code.python_pack import PythonPack


def test_imports_absolute_from_and_relative():
    src = b"import a.b\nimport x as y\nfrom c.d import e\nfrom . import f\nfrom .g import h\nfrom .. import i\n"
    got = PythonPack().imports("services/billing/seats.py", src)
    assert got == sorted(
        {"a.b", "x", "c.d", "c.d.e", "services.billing", "services.billing.f", "services.billing.g", "services.billing.g.h", "services", "services.i"}
    )


def test_imports_inside_functions_count():
    src = b"def f():\n    import json\n    from os import path\n"
    assert PythonPack().imports("m.py", src) == ["json", "os", "os.path"]
