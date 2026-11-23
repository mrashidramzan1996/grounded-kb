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
from kb.gitfiles import changed_files, default_branch, repo_files
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

    With changed_only, file-level checks (unassigned / doubly-assigned files, marker issues, module path
    suggestions) look only at files changed against `base` (default: the default branch); record-level
    gates still cover the whole KB."""
    root = Path(root)
    config = load_config(root / "kb.config.yaml")
    repo = KbRepo.load(root)
    if changed_only:
        base = base or default_branch(root)
        changed = changed_files(root, base)
    else:
        changed = None
    ctx = GateContext(root, config, repo, KbRepo.at_ref(root, base) if base else None)
    gates = list(DEFAULT_GATES) + ([decision_guard] if base else [])
    issues = validate_config(config, changed if changed is not None else repo_files(root))
    issues += run_gates(ctx, gates)
    if changed is not None:
        touched = set(changed)
        suggested = set(suggest_paths(root, config, repo, changed))
        issues = [
            i
            for i in issues
            if not (i.code == "marker.unknown_id" and i.path not in touched)
            and not (i.code == "module.paths_suggested" and i.record not in suggested)
        ]
    return CheckReport(sorted(issues, key=lambda i: (*i.sort_key, i.message)), config.policy.mode)
