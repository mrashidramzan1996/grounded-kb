"""Module paths are never guessed: they are suggested from `r[impl ID]` markers and claim anchors."""

from collections import defaultdict
from pathlib import Path, PurePosixPath

from kb.config.schema import KbConfig
from kb.config.validate import modules_for
from kb.gates.base import GateContext
from kb.gitfiles import repo_files
from kb.issues import Issue
from kb.markers import scan_markers
from kb.records.models import Feature, Requirement
from kb.records.repo import KbRepo


def _owners_by_id(repo: KbRepo) -> dict[str, set[str]]:
    """Module ids per requirement / feature / claim id."""
    out: dict[str, set[str]] = defaultdict(set)
    features = {r.id: r for r in repo.records.values() if isinstance(r, Feature)}
    for rec in repo.records.values():
        if isinstance(rec, Requirement):
            out[rec.id].update(rec.modules)
            out[rec.id].update(features[f].module for f in rec.features if f in features)
    for feat in features.values():
        out[feat.id].add(feat.module)
        for c in feat.claims:
            out[c.id].add(feat.module)
    return out


def _eligible(path: str) -> bool:
    parts = path.split("/")
    return parts[0] != "kb" and not any(p.startswith(".") for p in parts)


def _evidence(root: Path, repo: KbRepo, files: list[str]) -> dict[str, set[str]]:
    """module id -> evidence files (impl markers, claim code anchors)."""
    owners = _owners_by_id(repo)
    present = set(files)
    out: dict[str, set[str]] = defaultdict(set)
    for m in scan_markers(root, [f for f in files if _eligible(f)]):
        if m.kind == "impl":
            for mod in owners.get(m.id, ()):
                out[mod].add(m.path)
    for feat, claim in repo.claims():
        path = (claim.code or "").partition("#")[0]
        if path in present and _eligible(path):
            out[feat.module].add(path)
    return out


def _under(path: str, directory: str) -> bool:
    return path.startswith(directory + "/")


def _glob_dirs(dirs: set[str], foreign: list[str]) -> list[str]:
    """Merge directories into deepest common ancestors, never the repo root and never over foreign files."""

    def clean(d: str) -> bool:
        return not any(_under(f, d) for f in foreign)

    def common(a: str, b: str) -> str:
        pa, pb = PurePosixPath(a).parts, PurePosixPath(b).parts
        n = 0
        while n < min(len(pa), len(pb)) and pa[n] == pb[n]:
            n += 1
        return "/".join(pa[:n])

    cands = {d for d in dirs if clean(d)}
    while True:
        for a in sorted(cands):
            for b in sorted(cands):
                if a < b and (anc := common(a, b)) and clean(anc):
                    cands -= {a, b}
                    cands.add(anc)
                    break
            else:
                continue
            break
        else:
            return sorted(cands)


def _suggestions(root: Path, config: KbConfig, repo: KbRepo, files: list[str]) -> dict[str, tuple[list[str], int]]:
    evidence = _evidence(root, repo, files)
    candidates = [f for f in files if _eligible(f)]
    claimed: dict[str, set[str]] = defaultdict(set)  # file -> modules that own it
    for mod, fs in evidence.items():
        for f in fs:
            claimed[f].add(mod)
    for f in candidates:
        claimed[f].update(modules_for(f, config))
    out: dict[str, tuple[list[str], int]] = {}
    configured = {m.id for m in config.modules}
    for mod in sorted(evidence):
        if mod not in configured:
            continue  # unknown module ids are id_gate's business
        mine = {f for f in evidence[mod] if "/" in f}
        foreign = [f for f, owners in claimed.items() if owners - {mod}]
        dirs = _glob_dirs({f.rpartition("/")[0] for f in mine}, foreign)
        current = next((m.paths for m in config.modules if m.id == mod), [])
        globs = [f"{d}/**" for d in dirs if f"{d}/**" not in current]
        if globs:
            covered = sum(1 for f in mine if any(_under(f, g.removesuffix("/**")) for g in globs))
            out[mod] = (globs, covered)
    return out


def suggest_paths(root: Path, config: KbConfig, repo: KbRepo, files: list[str]) -> dict[str, list[str]]:
    return {m: globs for m, (globs, _) in _suggestions(Path(root), config, repo, files).items()}


def module_paths_gate(ctx: GateContext) -> list[Issue]:
    found = _suggestions(ctx.root, ctx.config, ctx.repo, repo_files(ctx.root))
    return [
        Issue(
            "module.paths_suggested",
            f"{mod}: implemented in {', '.join(g.removesuffix('**') for g in globs)} ({n} files)"
            f" → kb module paths {mod} --accept",
            "warning",
            record=mod,
        )
        for mod, (globs, n) in found.items()
    ]
