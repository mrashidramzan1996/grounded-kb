import pytest

from kb.config.load import ConfigError, load_config
from kb.yamlio import YamlError, dump_yaml, load_yaml_strict


def test_loads_spec_example(fixtures):
    c = load_config(fixtures / "configs/spec_example.yaml")
    assert [m.id for m in c.modules] == ["MOD-billing", "MOD-sessions"] and c.modules[0].risk == "high"
    assert c.teams["team-billing"].git_team == "@acme/billing" and c.sources[1].mapping["message"] == "body"
    assert c.visibility.restricted[0].readers == ["tech-leads"] and c.policy.mode == "warn-only"


def test_module_without_paths_is_valid(tmp_path):
    (p := tmp_path / "kb.config.yaml").write_text("version: 1\nmodules: [{id: MOD-billing, owner: '@acme/billing'}]\n")
    assert load_config(p).modules[0].paths == []


def test_defaults(tmp_path):
    (p := tmp_path / "kb.config.yaml").write_text("version: 1\n")
    c = load_config(p)
    assert c.evidence.required == {"low": "E2", "standard": "E4", "high": "E5"}
    assert c.policy.mode == "warn-only" and c.bots == ["*[bot]"]


@pytest.mark.parametrize("text,needle", [("version: 1\npolcy: {}\n", "polcy"), ("", "empty"),
    ("version: 1\nmodules: [{id: MOD-a, owner: t, risk: extreme}]\n", "risk"),
    ("version: 1\nversion: 1\n", "duplicate key"), ("version: 1\npolicy: {fail_on: [brokn]}\n", "brokn"),
    ("version: 1\nx: *nope\n", "nope")])
def test_rejects_bad_config(tmp_path, text, needle):
    (p := tmp_path / "kb.config.yaml").write_text(text)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert needle in str(e.value)


def test_dump_is_deterministic_and_keeps_order():
    data = {"b": 1, "a": [{"z": "x y", "c": None}], "long": "w " * 100}
    out = dump_yaml(data)
    assert out == dump_yaml(data) and out.index("b:") < out.index("a:")
    assert load_yaml_strict(out) == data and "\n  " not in out.split("long:")[1].strip()


def test_strict_yaml_errors():
    with pytest.raises(YamlError, match="duplicate key"):
        load_yaml_strict("a: 1\na: 2\n")
