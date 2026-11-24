import json

from helpers import apply_drafts, ctx, git, invoke, write
from kb.status import requirement_status

FEAT = """---
id: FEAT-001
title: Seats
module: MOD-billing
requirements: [REQ-F-001]
claims:
  - id: FEAT-001.C1
    claim: adds a seat
    requirement: REQ-F-001
    code: services/billing/seats.py#add_seat
    test: tests/test_seats.py::test_add
---
Seats.
"""


def drafted(acme_repo_ingested, fixtures):
    apply_drafts(acme_repo_ingested, fixtures / "drafts/acme-billing")
    return acme_repo_ingested


def test_status_before_and_after_code(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    assert set(requirement_status(*ctx(repo)[:3]).values()) == {"not_built"}
    write(repo, "services/billing/seats.py", "# r[impl REQ-F-002]\ndef remove_seat(s): return 0\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-002"] == "built"
    write(repo, "tests/test_seats.py", "# r[verify REQ-F-002]\ndef test_no_refund(): ...\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-002"] == "tested"


def test_verify_without_impl_is_not_built(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, "tests/test_x.py", "# r[verify REQ-F-002]\ndef test_x(): ...\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-002"] == "not_built"


def test_markers_in_kb_and_dot_dirs_ignored(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, ".hidden/a.py", "# r[impl REQ-F-002]\n")
    write(repo, "kb/notes.txt", "r[impl REQ-F-002]\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-002"] == "not_built"


def test_claim_anchor_and_test_link(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, "kb/features/FEAT-001.md", FEAT)
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "not_built"  # anchors do not resolve yet
    write(repo, "services/billing/seats.py", "def add_seat(): ...\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "built"
    write(repo, "tests/test_seats.py", "def test_add(): ...\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "tested"


def test_claim_marker_counts_for_its_requirement(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, "kb/features/FEAT-001.md", FEAT)
    write(repo, "a.py", "# r[impl FEAT-001.C1]\n")
    write(repo, "tests/t.py", "# r[verify FEAT-001.C1]\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "tested"


def test_unadapted_language_anchor_does_not_count(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, "kb/features/FEAT-001.md", FEAT.replace("services/billing/seats.py#add_seat", "web/a.ts#add"))
    write(repo, "web/a.ts", "export function add() {}\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "not_built"


def test_cli_text_and_json(built_acme):
    out = invoke(built_acme, "status")
    assert out.exit_code == 0, out.output
    lines = out.stdout.splitlines()
    assert lines[0].startswith("MOD-billing: ") and "built" in lines[0]
    assert [ln.split(":")[0] for ln in lines if ln.startswith("MOD-")] == ["MOD-billing", "MOD-sessions", "MOD-web"]
    assert lines[-1].startswith("total: ") and lines[-1].endswith("tested")
    data = json.loads(invoke(built_acme, "status", "--format", "json").stdout)
    assert data["requirements"]["REQ-F-001"] == "built"
    assert set(data) == {"requirements", "modules", "total"}
    assert data["total"]["built"] == sum(v != "not_built" for v in data["requirements"].values())
    assert data["total"]["total"] == len(data["requirements"])
    assert set(data["modules"]["MOD-billing"]) == {"built", "tested", "total"}
    assert list(data["requirements"]) == sorted(data["requirements"])


def test_cli_bad_format(built_acme):
    assert invoke(built_acme, "status", "--format", "xml").exit_code == 2


def test_claims_of_rejected_features_do_not_count(acme_repo_ingested, fixtures):
    repo = drafted(acme_repo_ingested, fixtures)
    write(repo, "kb/features/FEAT-001.md", FEAT.replace("---\nSeats.", "status: rejected\n---\nSeats."))
    write(repo, "services/billing/seats.py", "def add_seat(): ...\n")
    assert requirement_status(*ctx(repo)[:3])["REQ-F-001"] == "not_built"


def test_status_shows_verified_after_verify_on_clean_head(verify_repo):
    assert invoke(verify_repo, "verify", "--all").exit_code == 0
    assert git(verify_repo, "status", "--porcelain") == ""  # .kb/cache/status.json is gitignored: still clean
    r = invoke(verify_repo, "status")
    assert r.exit_code == 0, r.output
    lines = r.stdout.splitlines()
    assert lines[0] == "MOD-billing: 1/3 built, 1/3 tested, 1/3 verified"
    assert lines[-1] == "total: 1/3 built, 1/3 tested, 1/3 verified"
    assert "kb verify" not in r.stderr
    data = json.loads(invoke(verify_repo, "status", "--format", "json").stdout)
    assert data["modules"]["MOD-billing"]["verified"] == 1 and data["total"]["verified"] == 1


def test_status_without_fresh_verify_omits_verified(verify_repo):
    r = invoke(verify_repo, "status")
    assert "verified" not in r.stdout and "run `kb verify` for verified counts" in r.stderr
    assert invoke(verify_repo, "verify", "--all").exit_code == 0
    write(verify_repo, "services/billing/seats.py", "def remove_seat(account, seat): ...\n")  # dirty tree
    r = invoke(verify_repo, "status")
    assert "verified" not in r.stdout and "run `kb verify` for verified counts" in r.stderr
    git(verify_repo, "commit", "-qam", "edit")  # clean again, but HEAD moved past the verified commit
    r = invoke(verify_repo, "status")
    assert "verified" not in r.stdout and "run `kb verify` for verified counts" in r.stderr
    assert "verified" not in json.loads(invoke(verify_repo, "status", "--format", "json").stdout)["total"]
