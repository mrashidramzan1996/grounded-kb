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
    "Ask the user, offering its options. Record each fact the user gives about a requirement (a target, how to "
    "check it, which document is right) with `kb clarify` and write each handle into kb.config.yaml, then follow up "
    "as the option says. Never invent a target, quote, handle or approval."
)
# Critical 1 and Important 1-2 of the final review: when to commit, one redraft for all answers, when to stop,
# provenance, and never answering for the user
WORKFLOW_LINES = [
    "Answer owner and approver questions first: write the handles into kb.config.yaml and commit that file on its "
    "own before running any `kb redraft`.",
    "When the user answered several questions, record every answer with `kb clarify` first, then run one "
    "`kb redraft <ID> <ID> … --new` for all of them.",
    "After `kb ingest --report` or `kb redraft --report` passes, commit the changes (before the baseline: on the "
    "current branch; after it: on your pull-request branch).",
    "Run `kb todo` again. Repeat until there are no `do` items and no ask you have not yet put to the user in this "
    "session, then run `kb check --strict`.",
    "`--by` is the handle of the person who answered. If you do not know it, ask for it together with the "
    "questions; never guess it from git config or invent one.",
    "Never answer an ask or choose an option yourself, even when a document seems to settle it; only the user answers.",
]
# an agent committed drafts that `kb ingest --report` rejected (agent evaluation, 0.3.0)
NEVER_COMMIT_REJECTED = (
    "Never commit while `kb ingest --report` or `kb redraft --report` shows a rejection. If a rejection looks wrong "
    "to you, stop and tell the user; do not work around it."
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
        f"{len(PLAYBOOK)}. Before the baseline exists, work on the current branch."
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
    assert not (acme_repo / ".claude/settings.json").exists()  # the skill is refused before the hooks are merged


def test_skill_has_an_example_exchange(acme_repo):
    get_agent("claude-code").install(acme_repo)
    text = (acme_repo / SKILL).read_text()
    assert "1a, 2a" in text and "{{" not in text
    assert "evidence: `docs/requirements.md`, page 2, `Performance`: `The page should load quickly.`" in text
    assert "--by @jane" in text


def test_playbook_single_source(tmp_path, acme_repo):
    (tmp_path / "plain").mkdir()
    agents = _agents_md(tmp_path / "plain")
    get_agent("claude-code").install(acme_repo)
    skill = (acme_repo / SKILL).read_text()
    assert len(PLAYBOOK) >= 6
    for line in PLAYBOOK + AGENT_RULES:
        assert line in agents and line in skill


def test_playbook_says_when_to_commit_and_when_to_stop():
    text = "\n".join(PLAYBOOK)
    for line in WORKFLOW_LINES:
        assert line in text
    assert "until it is clean" not in text and "state.clean" not in text
    numbers = [int(line.split(".", 1)[0]) for line in PLAYBOOK]
    assert numbers == list(range(1, len(PLAYBOOK) + 1))
    # the order an agent meets them in: commit after a report, owners before a redraft, every answer in one redraft
    after_report = text.index(WORKFLOW_LINES[2])
    assert text.index("Do every `do` item") < after_report < text.index("Collect every `ask` item")
    assert text.index(WORKFLOW_LINES[0]) < text.index(WORKFLOW_LINES[1]) < text.index(WORKFLOW_LINES[3])


def test_skill_example_follows_the_playbook(acme_repo):
    get_agent("claude-code").install(acme_repo)
    text = (acme_repo / SKILL).read_text()
    example = text[text.index("## Example exchange") : text.index("## Rules")]
    assert "--by @jane" in example and "<the user's handle>" not in example
    assert "kb.config.yaml" in example and "on its own" in example  # the approver handle is not a kb clarify
    assert example.count("kb clarify") == 1
    assert "kb redraft REQ-NF-001 --new" in example and "kb redraft --report" in example and "commit" in example
    assert "Who answered" in example or "your forge handle" in example


OLD_BLOCK = "<!-- kb:begin kb -->\nWorking rules:\n\n" + "\n".join(AGENT_RULES[:5]) + "\n<!-- kb:end kb -->\n"


@pytest.mark.parametrize("agent", ["codex", "claude-code", "generic"])
def test_agents_install_updates_an_old_agents_block(acme_repo, agent):
    before, after = "# My project\n\nOur own notes.\n\n", "\n## Later\n\nMore of our notes.\n"
    (acme_repo / "AGENTS.md").write_text(before + OLD_BLOCK + after)
    r = invoke(acme_repo, "agents", "install", agent)
    assert r.exit_code == 0, r.output
    assert "updated AGENTS.md (kb block)" in r.stdout.splitlines()
    text = (acme_repo / "AGENTS.md").read_text()
    assert text.startswith(before) and text.endswith(after)
    for line in PLAYBOOK + AGENT_RULES:
        assert line in text
    assert text.count("<!-- kb:begin kb -->") == 1
    again = invoke(acme_repo, "agents", "install", agent)
    assert "AGENTS.md is up to date" in again.stdout.splitlines()
    assert (acme_repo / "AGENTS.md").read_text() == text


def test_agents_install_creates_agents_md(tmp_path):
    repo = make_git_repo(None, tmp_path)
    r = invoke(repo, "agents", "install", "codex")
    assert r.exit_code == 0, r.output
    assert "updated AGENTS.md (kb block)" in r.stdout.splitlines()
    assert PLAYBOOK[-1] in (repo / "AGENTS.md").read_text()
    (tmp_path / "init").mkdir()
    assert (repo / "AGENTS.md").read_text() == _agents_md(tmp_path / "init")  # what kb init writes


def test_agents_install_refuses_a_symlinked_agents_md(acme_repo, tmp_path):
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("theirs\n")
    (acme_repo / "AGENTS.md").unlink(missing_ok=True)
    (acme_repo / "AGENTS.md").symlink_to(elsewhere)
    r = invoke(acme_repo, "agents", "install", "codex")
    assert r.exit_code == 2 and "AGENTS.md" in r.stderr
    assert elsewhere.read_text() == "theirs\n"
    assert not (acme_repo / ".codex/hooks.json").exists()  # refused before anything is written


def test_playbook_says_never_commit_a_rejection():
    commit_step = next(n for n, line in enumerate(PLAYBOOK) if WORKFLOW_LINES[2] in line)
    assert PLAYBOOK[commit_step + 1] == f"{commit_step + 2}. {NEVER_COMMIT_REJECTED}"


# agent evaluation, 0.3.0: an agent wrote a generator script into /tmp to write the records, and after the baseline
# an agent committed new work straight onto main
NO_SCRIPTS = "Write each record file with your file-editing tool; do not write or run scripts that generate records."
BRANCH_STEP = (
    "Before the baseline exists, work on the current branch. After it, make changes on a branch and open a pull "
    "request; never merge it yourself. `kb todo` puts a `branch` item first when you must start that branch: run its "
    "command before any other `do` item."
)


def test_playbook_says_write_records_with_file_tools():
    assert PLAYBOOK[1].startswith("2. Do every `do` item") and PLAYBOOK[1].endswith(f" {NO_SCRIPTS}")


def test_playbook_says_when_to_start_the_branch():
    assert PLAYBOOK[-1] == f"{len(PLAYBOOK)}. {BRANCH_STEP}"


# 0.4.0: the extraction evaluation, after the commit rules and before the asks
EVAL_STEPS = [
    "Before freezing a baseline, and after adding or changing documents, evaluate the extraction: when `kb todo` lists "
    "`eval:` items, follow them in order; do the blind extraction in a fresh session or subagent that does not read the "
    "existing records.",
    "Judge each evaluation item on its own and over-flag; never decide a finding yourself — ask the user, then record "
    "their answer with `kb eval decide --by <their handle>`.",
    "Put the output of `kb eval sample` in every pull-request description after the baseline.",
]


def test_playbook_has_eval_steps(tmp_path, acme_repo):
    assert PLAYBOOK[3] == f"4. {NEVER_COMMIT_REJECTED}"  # inserted after step 4
    assert PLAYBOOK[4:7] == [f"{n}. {line}" for n, line in enumerate(EVAL_STEPS, start=5)]
    assert PLAYBOOK[7].startswith("8. Collect every `ask` item.")
    (tmp_path / "plain").mkdir()
    agents = _agents_md(tmp_path / "plain")
    get_agent("claude-code").install(acme_repo)
    skill = (acme_repo / SKILL).read_text()
    for line in EVAL_STEPS:
        assert line in agents and line in skill
