import hashlib
from pathlib import Path

from kb.gates.base import GateContext
from kb.issues import Issue
from kb.sources.store import MAX_SOURCE_BYTES


def sha256_file(path: Path) -> str | None:
    """sha256 of the file, or None when it is missing; raises ValueError over MAX_SOURCE_BYTES."""
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"larger than {MAX_SOURCE_BYTES} bytes (MAX_SOURCE_BYTES)")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def source_gate(ctx: GateContext) -> list[Issue]:
    issues = []
    root = ctx.root.resolve()
    for src_id, src in sorted(ctx.repo.sources.items()):
        where = f"kb/sources/{src_id}/source.yaml"
        original = ctx.root / src.original
        if original.is_symlink() or not original.resolve().is_relative_to(root):
            issues.append(Issue(
                "source.invalid_original", f"{src.original} is a symlink or lies outside the repository",
                path=where, record=src_id,
            ))
            continue
        try:
            digest = sha256_file(original)
        except ValueError as e:
            issues.append(Issue("source.outdated", f"{src.original} is {e}", path=where, record=src_id))
        else:
            if digest != src.original_sha256:
                issues.append(Issue(
                    "source.outdated", f"{src.original} is missing or changed; run `kb source sync`",
                    path=where, record=src_id,
                ))
        if src.guard.secrets > 0:
            issues.append(Issue(
                "source.secret_in_original",
                f"{src.original} contains {src.guard.secrets} secret(s); remove them from the original",
                path=where, record=src_id,
            ))
    return issues
