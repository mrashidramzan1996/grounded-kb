import pytest
from helpers import mod

from kb.config.edit import add_module, set_paths
from kb.config.load import ConfigError, load_config


def _cfg(root):
    return load_config(root / "kb.config.yaml")


def test_add_module_keeps_comments(acme_repo):
    (acme_repo / "kb.config.yaml").write_text("version: 1  # keep me\nmodules: []\n")
    add_module(acme_repo, "MOD-billing", owner="@acme/billing", risk="high")
    t = (acme_repo / "kb.config.yaml").read_text()
    assert "# keep me" in t and _cfg(acme_repo).modules[0].paths == []
    assert _cfg(acme_repo).modules[0].risk == "high"


def test_add_module_with_title_and_paths_appends(acme_repo):
    add_module(acme_repo, "MOD-a", owner="product-owners")
    add_module(acme_repo, "MOD-b", owner="@x/y", title="B", paths=["b/**"])
    mods = _cfg(acme_repo).modules
    assert [m.id for m in mods] == ["MOD-a", "MOD-b"] and mods[1].title == "B" and mods[1].paths == ["b/**"]


def test_add_module_todo_owner_is_allowed(acme_repo):
    add_module(acme_repo, "MOD-a", owner="TODO")
    assert _cfg(acme_repo).modules[0].owner == "TODO"


@pytest.mark.parametrize("bad", ["billing", "MOD-Billing", "MOD-"])
def test_add_module_rejects_bad_id_without_writing(acme_repo, bad):
    before = (acme_repo / "kb.config.yaml").read_text()
    with pytest.raises(ConfigError):
        add_module(acme_repo, bad, owner="@a/b")
    assert (acme_repo / "kb.config.yaml").read_text() == before


def test_add_module_rejects_unresolvable_owner(acme_repo):
    with pytest.raises(ConfigError, match="owner"):
        add_module(acme_repo, "MOD-a", owner="nobody")


def test_add_module_rejects_duplicate(acme_repo):
    add_module(acme_repo, "MOD-a", owner="@a/b")
    with pytest.raises(ConfigError, match="MOD-a"):
        add_module(acme_repo, "MOD-a", owner="@a/b")
    assert len(_cfg(acme_repo).modules) == 1


def test_add_module_rolls_back_when_result_does_not_load(acme_repo):
    before = (acme_repo / "kb.config.yaml").read_text()
    with pytest.raises(ConfigError):
        add_module(acme_repo, "MOD-a", owner="@a/b", risk="reckless")
    assert (acme_repo / "kb.config.yaml").read_text() == before


def test_set_paths_replaces_and_keeps_comments(acme_repo):
    (acme_repo / "kb.config.yaml").write_text(
        "version: 1\nmodules:\n  # billing\n  - id: MOD-billing  # keep\n    owner: '@a/b'\n"
    )
    set_paths(acme_repo, "MOD-billing", ["svc/**", "web/**"])
    assert "# keep" in (acme_repo / "kb.config.yaml").read_text()
    assert _cfg(acme_repo).modules[0].paths == ["svc/**", "web/**"]
    set_paths(acme_repo, "MOD-billing", ["svc/**"])
    assert _cfg(acme_repo).modules[0].paths == ["svc/**"]


def test_set_paths_unknown_module(acme_repo):
    with pytest.raises(ConfigError, match="MOD-x"):
        set_paths(acme_repo, "MOD-x", ["a/**"])
