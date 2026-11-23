from kb.agents.claude_code import ClaudeCode
from kb.agents.codex import Codex
from kb.agents.generic import Generic
from kb.ports.agents import AgentIntegration

_AGENTS: dict[str, AgentIntegration] = {a.name: a for a in (ClaudeCode(), Codex(), Generic())}


def agent_names() -> list[str]:
    return list(_AGENTS)


def get_agent(name: str) -> AgentIntegration:
    try:
        return _AGENTS[name]
    except KeyError:
        raise KeyError(f"unknown agent {name!r}: use {', '.join(_AGENTS)}") from None
