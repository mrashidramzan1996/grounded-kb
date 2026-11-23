import importlib.metadata

from helpers import invoke


def test_version_matches_metadata(tmp_path):
    r = invoke(tmp_path, "version")
    assert r.exit_code == 0 and r.stdout.strip() == importlib.metadata.version("grounded-kb")


def test_help_lists_version(tmp_path):
    assert "version" in invoke(tmp_path, "--help").stdout
