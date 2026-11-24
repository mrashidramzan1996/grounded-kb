from kb.deps import (
    changed_packages,
    imported_top_levels,
    is_lockfile,
    lockfile_packages,
    module_name,
    normalise_package,
)


def test_module_name_init_and_non_python():
    assert module_name("services/billing/seats.py") == "services.billing.seats"
    assert module_name("pkg/__init__.py") == "pkg"
    assert module_name("README.md") is None


def test_is_lockfile():
    assert is_lockfile("uv.lock") and is_lockfile("a/b/poetry.lock") and is_lockfile("x/requirements-dev.txt")
    assert not is_lockfile("notes.txt")


def test_uv_lock_and_poetry_lock_parse():
    toml = b'[[package]]\nname = "Stripe"\nversion = "7.1.0"\n\n[[package]]\nname = "python-dateutil"\nversion = "2.9"\n'
    want = {"stripe": "7.1.0", "python_dateutil": "2.9"}
    assert lockfile_packages("uv.lock", toml) == want
    assert lockfile_packages("svc/poetry.lock", toml) == want


def test_requirements_txt_parse_ignores_comments_and_markers():
    data = b'# c\n-r other.txt\n-e .\nstripe==7.1.0 ; python_version>"3"\nFoo-Bar==1.0  # pinned\n'
    assert lockfile_packages("requirements.txt", data) == {"stripe": "7.1.0", "foo_bar": "1.0"}


def test_changed_packages_added_removed_bumped():
    assert changed_packages({"a": "1", "b": "1", "c": "1"}, {"a": "1", "b": "2", "d": "1"}) == {"b", "c", "d"}


def test_normalise_package():
    assert normalise_package("Python-Dateutil") == "python_dateutil"
    assert normalise_package("zope.interface") == "zope_interface"


def test_imported_top_levels():
    assert imported_top_levels(["Python_Dateutil.parser", "stripe", "a.b"]) == {"python_dateutil", "stripe", "a"}
