import pytest

from helpers import invoke, make_git_repo
from kb.agents.registry import get_agent
from kb.init.scaffold import init_repo
from kb.rules import AGENT_RULES, PLAYBOOK
from kb.safewrite import WriteRefused
from kb.yamlio import load_yaml_strict

SKILL = ".claude/skills/kb/SKILL.md"
MARKER = "<!-- written by kb -->"
RULE_6 = (
    "6. Drive the knowledge base yourself: run `kb todo`, do every item under Do, and ask the user every item under "
    "Ask the user, offering its options. Record each answer with `kb clarify`, then follow up as the option says. "
    "Never invent a target, quote or approval."
)


def _agents_md(tmp_path):
    repo = make_git_repo(None, tmp_path)
    init_repo(repo)
    return (repo / "AGENTS.md").read_text()


def test_rules_include_rule_6():
    assert len(AGENT_RULES) == 6 and AGENT_RULES[5] == RULE_6


def test_agents_block_contains_playbook(tmp_path):
    agents = _agents_md(tmp_path)
    block = agents[agents.index("<!-- kb:begin") : agents.index("<!-- kb:end")]
    assert RULE_6 in block
    heading = block.index("## Working with the knowledge base")
    assert heading > block.index(AGENT_RULES[-1])
    steps = block[heading:]
    assert steps.index("1. When the user asks you to set up or update the knowledge base") < steps.index(
        "6. Before the baseline exists, work on the current branch."
    )
    assert "run `kb todo --format json`" in steps and "never merge it yourself" in steps
    assert "do not ask it again in the same session" in steps


def test_claude_code_install_writes_skill(acme_repo):
    lines = get_agent("claude-code").install(acme_repo)
    text = (acme_repo / SKILL).read_text()
    assert text.startswith("---\nname: kb\ndescription: Set up or update the product knowledge base (kb) from documents,")
    assert MARKER in text and f"wrote {SKILL}" in lines
    front = load_yaml_strict(text.split("---\n")[1])
    assert front["name"] == "kb" and front["description"].endswith("or a changed or new document.")
    assert (acme_repo / ".claude/settings.json").exists()
    assert get_agent("claude-code").install(acme_repo)[-1] == f"{SKILL} already up to date"
    assert (acme_repo / SKILL).read_text() == text


def test_skill_overwritten_when_it_carries_the_marker(acme_repo):
    path = acme_repo / SKILL
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: kb\n---\n{MARKER}\nold playbook\n")
    get_agent("claude-code").install(acme_repo)
    assert "old playbook" not in path.read_text() and PLAYBOOK[0] in path.read_text()


def test_skill_not_overwritten_without_marker(acme_repo):
    path = acme_repo / SKILL
    path.parent.mkdir(parents=True)
    path.write_text("my own kb skill\n")
    with pytest.raises(WriteRefused, match="not written by kb"):
        get_agent("claude-code").install(acme_repo)
    assert path.read_text() == "my own kb skill\n"
    assert not (acme_repo / ".claude/settings.json").exists()  # nothing written
    result = invoke(acme_repo, "agents", "install", "claude-code")
    assert result.exit_code == 2 and SKILL in result.stderr


def test_skill_refuses_a_symlink(acme_repo, tmp_path):
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text(MARKER)
    path = acme_repo / SKILL
    path.parent.mkdir(parents=True)
    path.symlink_to(elsewhere)
    assert invoke(acme_repo, "agents", "install", "claude-code").exit_code == 2
    assert elsewhere.read_text() == MARKER


def test_skill_has_an_example_exchange(acme_repo):
    get_agent("claude-code").install(acme_repo)
    text = (acme_repo / SKILL).read_text()
    assert "1a, 2a" in text and "{{" not in text


def test_playbook_single_source(tmp_path, acme_repo):
    (tmp_path / "plain").mkdir()
    agents = _agents_md(tmp_path / "plain")
    get_agent("claude-code").install(acme_repo)
    skill = (acme_repo / SKILL).read_text()
    assert len(PLAYBOOK) >= 6
    for line in PLAYBOOK + AGENT_RULES:
        assert line in agents and line in skill
