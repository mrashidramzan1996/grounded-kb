from pathlib import Path

import pytest


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    from kb.config.schema import KbConfig

    def build(modules=(), sources=(), **extra):
        return KbConfig.model_validate(
            {
                "version": 1,
                "teams": {"team-a": {"git_team": "@acme/a"}},
                "modules": [m.model_dump() for m in modules],
                "sources": [{"paths": [p]} for p in sources],
                "policy": {"approvers": {"intent": ["team-a"], "decisions": ["team-a"]}},
                **extra,
            }
        )

    return build
