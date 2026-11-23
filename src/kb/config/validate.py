from fnmatch import fnmatch

from pathspec import GitIgnoreSpec

from kb.code.registry import adapter_for
from kb.config.schema import KbConfig
from kb.issues import Issue

_EXEMPT_NAMES = {"kb.config.yaml", "AGENTS.md", "CLAUDE.md", "CODEOWNERS"}


_TEST_DIRS = {"tests", "test", "__tests__", "spec"}
_TEST_NAMES = ["test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*"]


def is_test_file(path: str) -> bool:
    """Test files are verification evidence, not implementation: they need no module."""
    *dirs, name = path.split("/")
    return bool(_TEST_DIRS & set(dirs)) or any(fnmatch(name, p) for p in _TEST_NAMES)


def resolve_owner(owner: str, config: KbConfig) -> str | None:
    if owner in config.teams:
        return config.teams[owner].git_team
    if owner.startswith("@"):
        return owner
    return None


def _spec(patterns: list[str]) -> GitIgnoreSpec:
    return GitIgnoreSpec.from_lines(patterns)


def modules_for(path: str, config: KbConfig) -> list[str]:
    return [m.id for m in config.modules if m.paths and _spec(m.paths).match_file(path)]


def validate_config(config: KbConfig, files: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    for m in config.modules:
        if m.id in seen:
            issues.append(Issue("config.duplicate_module", f"module {m.id} is defined more than once", record=m.id))
        seen.add(m.id)
        issues.extend(_owner_issues(m.owner, f"module {m.id}", config, m.id))
    for kind, entries in (("intent", config.policy.approvers.intent), ("decisions", config.policy.approvers.decisions)):
        for entry in entries:
            issues.extend(_owner_issues(entry, f"policy.approvers.{kind}", config, None))

    exempt = _spec(
        ["kb/**", *_EXEMPT_NAMES]
        + [p for s in config.sources for p in s.paths]
        + config.ignore
        + config.generated
    )
    for path in sorted(files):
        if exempt.match_file(path) or any(seg.startswith(".") for seg in path.split("/")):
            continue
        mods = modules_for(path, config)
        if len(mods) > 1:
            issues.append(Issue("config.file_in_two_modules", f"{path} belongs to modules {', '.join(mods)}", path=path))
        elif not mods and adapter_for(path) is not None and not is_test_file(path):
            issues.append(Issue("config.file_unassigned", f"{path} is not assigned to any module", "warning", path=path))
    return issues


def _owner_issues(owner: str, where: str, config: KbConfig, record: str | None) -> list[Issue]:
    if owner == "TODO":
        return [Issue("config.owner_todo", f"{where}: owner is still TODO", record=record)]
    if resolve_owner(owner, config) is None:
        return [Issue("config.unknown_owner", f"{where}: '{owner}' is neither a team key nor an @handle", record=record)]
    return []
