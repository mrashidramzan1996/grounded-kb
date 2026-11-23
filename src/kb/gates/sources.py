import hashlib

from kb.gates.base import GateContext
from kb.issues import Issue


def source_gate(ctx: GateContext) -> list[Issue]:
    issues = []
    for src_id, src in sorted(ctx.repo.sources.items()):
        where = f"kb/sources/{src_id}/source.yaml"
        original = ctx.root / src.original
        if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != src.original_sha256:
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
