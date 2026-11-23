from dataclasses import dataclass
from pathlib import Path

from kb.config.load import load_config
from kb.config.validate import validate_config
from kb.gates.anchors import anchor_gate
from kb.gates.base import GateContext, run_gates
from kb.gates.decisions import decision_guard
from kb.gates.ids import id_gate
from kb.gates.quotes import quote_gate
from kb.gates.sources import source_gate
from kb.gates.triage import triage_gate
from kb.config.load import ConfigError
from kb.gitfiles import GitError, changed_since, checkout_at, merge_base, repo_files, resolve_base
from kb.issues import Issue
from kb.modules import module_paths_gate, suggest_paths
from kb.records.repo import KbRepo

# id_gate must always run: KbRepo does not enforce that a file name matches its record id.
DEFAULT_GATES = [id_gate, quote_gate, anchor_gate, source_gate, module_paths_gate, triage_gate]


@dataclass
class CheckReport:
    issues: list[Issue]
    mode: str


def run_check(root: Path, *, base: str | None = None, changed_only: bool = False) -> CheckReport:
    """Every Phase 1 gate. Raises ConfigError / GitError; the caller maps them to exit 2.

    The base is where the branch forked from `base`, or with changed_only and no `base`, from the default
    branch. With changed_only, file-level checks (unassigned / doubly-assigned files, marker issues, module
    path suggestions) look only at files changed against it; record-level gates still cover the whole KB.
    When no base resolves (no default branch yet), every file is checked and every source re-derived."""
    root = Path(root)
    config = load_config(root / "kb.config.yaml")
    repo = KbRepo.load(root)
    # decision_guard and the source gate compare with where the branch forked, not the tip of the base branch
    base_ref = merge_base(root, base) if base else resolve_base(root) if changed_only else None
    changed = changed_since(root, base_ref) if changed_only and base_ref else None
    base_repo = KbRepo.at_ref(root, base_ref) if base_ref else None
    ctx = GateContext(root, config, repo, base_repo, base_ref)
    gates = list(DEFAULT_GATES) + ([decision_guard] if base_ref else [])
    # a change to the config or to kb/ can break file-level checks on untouched files: then check every file
    narrow = changed is not None and not any(p == "kb.config.yaml" or p.startswith("kb/") for p in changed)
    if narrow:
        changed = [p for p in changed if (root / p).exists()]
    issues = validate_config(config, changed if narrow else repo_files(root))
    issues += run_gates(ctx, gates)
    if narrow:
        touched = set(changed)
        suggested = set(suggest_paths(root, config, repo, changed))
        issues = [
            i
            for i in issues
            if not (i.code == "marker.unknown_id" and i.path not in touched)
            and not (i.code == "module.paths_suggested" and i.record not in suggested)
        ]
    return CheckReport(sorted(issues, key=lambda i: (*i.sort_key, i.message)), config.policy.mode)


def errors_at(root: Path, commit: str) -> list[Issue]:
    """The errors `kb check` reported at `commit`, from a temporary checkout of it. No config there: none."""
    with checkout_at(Path(root), commit) as tree:
        if not (tree / "kb.config.yaml").exists():
            return []
        try:
            report = run_check(tree, base=commit)
        except (ConfigError, GitError):
            return []  # nothing at the base can excuse an error now
    return [i for i in report.issues if i.severity == "error"]
