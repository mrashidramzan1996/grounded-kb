from helpers import mod

from kb.config.schema import KbConfig
from kb.records.models import (
    Evidence,
    Feature,
    FeatureClaim,
    Invariant,
    Quote,
    Requirement,
)
from kb.records.repo import KbRepo
from kb.verify.affected import Affected, affected
from kb.verify.changes import ChangeSet
from kb.verify.index import AnchorIndex, FileCard

CONFIG = KbConfig(version=1, modules=[mod("MOD-billing", ["billing/**"]), mod("MOD-sessions", ["sessions/**"])])


def card(path, modules=(), imports=(), markers=()):
    return FileCard(path=path, modules=tuple(modules), symbols=(), imports=tuple(imports), markers=tuple(markers))


def cs(files=(), segments=None, packages=()):
    return ChangeSet("main", tuple(sorted(files)), {}, {}, segments or {}, frozenset(packages))


def ev(seg="SRC-a#seg-01"):
    return Evidence(quotes=[Quote(source=seg, text="x")])


def build(extra=()):
    repo = KbRepo()
    recs = [
        Requirement(id="REQ-F-001", statement="s", evidence=ev("SRC-a#seg-01"), features=["FEAT-001"]),
        Requirement(id="REQ-F-002", statement="s", evidence=ev("SRC-a#seg-02"), features=["FEAT-002"]),
        Feature(id="FEAT-001", title="t", module="MOD-billing", requirements=["REQ-F-001"], claims=[
            FeatureClaim(id="FEAT-001.C1", claim="c", requirement="REQ-F-001",
                         code="billing/seats.py#f", test="tests/test_seats.py::test_a")]),
        Feature(id="FEAT-002", title="t", module="MOD-sessions", requirements=["REQ-F-002"], claims=[
            FeatureClaim(id="FEAT-002.C1", claim="c", requirement="REQ-F-002",
                         code="sessions/s.py#g", test="tests/test_s.py::test_b")]),
        Invariant(id="INV-001", statement="s", governs=["MOD-billing"], tests=["tests/test_inv.py::test_i"]),
        *extra,
    ]
    for r in recs:
        repo.records[r.id] = r
        repo.paths[r.id] = f"kb/{r.id}.md"
    index = AnchorIndex({
        "billing/seats.py": card("billing/seats.py", ["MOD-billing"], imports=["stripe"]),
        "billing/api.py": card("billing/api.py", ["MOD-billing"], imports=["billing.seats"]),
        "sessions/s.py": card("sessions/s.py", ["MOD-sessions"], imports=["redis"]),
        "tests/test_seats.py": card("tests/test_seats.py"),
    })
    return repo, index


def run(changes, extra=()):
    repo, index = build(extra)
    return affected(changes, CONFIG, repo, index)


def test_anchor_file_change_affects_claim_and_requirement():
    a = run(cs(["billing/seats.py"]))
    assert a.claims == {"FEAT-001.C1"}
    assert a.requirements == {"REQ-F-001"}
    assert a.invariants == frozenset()


def test_linked_test_file_change_affects_claim():
    assert run(cs(["tests/test_seats.py"])).claims == {"FEAT-001.C1"}
    assert run(cs(["tests/test_inv.py"])).invariants == {"INV-001"}


def test_marker_file_affects_claim():
    repo, index = build()
    index.cards["billing/extra.py"] = card("billing/extra.py", markers=[("impl", "FEAT-002.C1", 1, None)])
    a = affected(cs(["billing/extra.py"]), CONFIG, repo, index)
    assert a.claims == {"FEAT-002.C1"} and a.requirements == {"REQ-F-002"}


def test_lockfile_bump_affects_only_importing_module():
    a = run(cs(["uv.lock"], packages=["stripe"]))
    assert a.claims == {"FEAT-001.C1"}
    assert a.requirements == {"REQ-F-001"}
    assert a.invariants == {"INV-001"}  # governs MOD-billing


def test_segment_change_affects_citing_requirements():
    a = run(cs(segments={"SRC-a#seg-02": "changed"}))
    assert a.requirements == {"REQ-F-002"}
    assert a.claims == frozenset()


def test_record_file_change():
    assert run(cs(["kb/FEAT-002.md"])).claims == {"FEAT-002.C1"}
    assert run(cs(["kb/INV-001.md"])).invariants == {"INV-001"}


def test_importer_hop():
    repo, index = build()
    repo.records["FEAT-001"].claims[0].code = "billing/api.py#h"
    a = affected(cs(["billing/seats.py"]), CONFIG, repo, index)
    assert "FEAT-001.C1" in a.claims


def test_config_change_affects_everything():
    a = run(cs(["kb.config.yaml"]))
    assert a == run(None)


def test_all_mode():
    a = run(None)
    assert a == Affected(
        frozenset({"FEAT-001.C1", "FEAT-002.C1"}), frozenset({"REQ-F-001", "REQ-F-002"}), frozenset({"INV-001"})
    )


def test_superseded_records_not_affected():
    old = Invariant(id="INV-002", statement="s", governs=["MOD-billing"], supersedes="INV-001")
    extra = [old, Feature(id="FEAT-003", title="t", module="MOD-billing", status="rejected", claims=[
        FeatureClaim(id="FEAT-003.C1", claim="c", code="billing/seats.py#f")])]
    a = run(cs(["billing/seats.py", "kb/INV-001.md"]), extra)
    assert "INV-001" not in a.invariants
    assert "FEAT-003.C1" not in a.claims
    assert "FEAT-003.C1" not in run(None, extra).claims
    assert "INV-001" not in run(None, extra).invariants


def test_unmatched_package_rechecks_modules_with_python_files():
    a = run(cs(["uv.lock"], packages=["python_dateutil"]))
    assert a.claims == {"FEAT-001.C1", "FEAT-002.C1"}
    assert a.invariants == {"INV-001"}


def test_invariant_test_importing_changed_file_is_affected():
    repo, index = build()
    index.cards["tests/test_inv.py"] = card("tests/test_inv.py", imports=["billing.seats"])
    a = affected(cs(["billing/seats.py"]), CONFIG, repo, index)
    assert "INV-001" in a.invariants


def test_requirement_module_ignores_non_live_feature():
    repo, index = build()
    repo.records["FEAT-009"] = Feature(id="FEAT-009", title="t", module="MOD-billing", status="rejected")
    repo.records["REQ-F-009"] = Requirement(id="REQ-F-009", statement="s", evidence=ev("SRC-a#seg-09"), features=["FEAT-009"])
    repo.paths.update({"FEAT-009": "kb/FEAT-009.md", "REQ-F-009": "kb/REQ-F-009.md"})
    a = affected(cs(["uv.lock"], packages=["stripe"]), CONFIG, repo, index)
    assert "REQ-F-009" not in a.requirements
