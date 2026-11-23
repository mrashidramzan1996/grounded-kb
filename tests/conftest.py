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


@pytest.fixture
def acme_kb_with(acme_repo):
    """acme_kb_with(*records) -> GateContext over acme_repo with its docs added and the records written under kb/."""
    from helpers import add_all
    from kb.config.load import load_config
    from kb.gates.base import GateContext
    from kb.records.repo import KbRepo
    from kb.yamlio import dump_yaml

    def build(*records):
        add_all(acme_repo)
        cfg_path = acme_repo / "kb.config.yaml"
        cfg_path.write_text(
            ACME_CONFIG.replace("modules: []", "modules:\n  - {id: MOD-billing, owner: tech-leads}")
        )
        repo = KbRepo.load(acme_repo)

        def segment_id(stem: str, suffix: str, text: str) -> str:
            """The first segment under the heading holding `text` (else the first under the heading)."""
            text_md = repo.source_text(f"SRC-{stem}")
            segs = [s for s in repo.segments(f"SRC-{stem}") if s.heading.endswith(suffix)]
            return next((s for s in segs if text in text_md[s.start : s.end]), segs[0]).id

        for r in records:
            rec = r(segment_id)
            out = acme_repo / "kb/requirements" / f"{rec.id}.yaml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(dump_yaml(rec.model_dump(mode="json", exclude_defaults=True)))
        return GateContext(acme_repo, load_config(cfg_path), KbRepo.load(acme_repo))

    return build
