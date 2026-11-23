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


@pytest.fixture
def spec_req() -> dict:
    """The spec §10 Requirement example, minus derived fields."""
    return {
        "id": "REQ-F-034",
        "schema_version": 1,
        "statement": "No refund is given when a seat is removed.",
        "evidence": {
            "mode": "all",
            "quotes": [{"source": "SRC-pricing-spec-v3#seg-08", "text": "No refund is given."}],
        },
        "features": ["FEAT-014"],
    }


def _kb_valid_config(root: Path):
    from kb.config.load import load_config

    return load_config(root / "kb.config.yaml")


@pytest.fixture
def valid_ctx(fixtures):
    from helpers import load_dir
    from kb.gates.base import GateContext
    from kb.records.repo import KbRepo

    root = fixtures / "kb_valid"
    return GateContext(root, _kb_valid_config(root), KbRepo.from_files(load_dir(root)))


@pytest.fixture
def valid_ctx_with(fixtures):
    """valid_ctx_with(*mutations) -> GateContext built from kb_valid after applying each mutation."""
    from helpers import load_dir
    from kb.gates.base import GateContext
    from kb.records.repo import KbRepo

    def build(*mutations):
        root = fixtures / "kb_valid"
        files = load_dir(root)
        for m in mutations:
            m(files)
        return GateContext(root, _kb_valid_config(root), KbRepo.from_files(files))

    return build


@pytest.fixture
def valid_git_ctx(fixtures, tmp_path):
    from helpers import GitCtx, make_git_repo

    root = make_git_repo(fixtures / "kb_valid", tmp_path)
    return GitCtx(root, _kb_valid_config(root))


ACME_CONFIG = """\
version: 1
teams:
  product-owners: {git_team: "@acme/product"}
  tech-leads: {git_team: "@acme/tech-leads"}
policy:
  approvers:
    intent: [product-owners]
    decisions: [tech-leads]
sources:
  - paths: ["docs/**"]
modules: []
"""


@pytest.fixture
def acme_repo(fixtures, tmp_path):
    from helpers import make_git_repo

    return make_git_repo(fixtures / "repos/acme-billing", tmp_path, extra={"kb.config.yaml": ACME_CONFIG})
