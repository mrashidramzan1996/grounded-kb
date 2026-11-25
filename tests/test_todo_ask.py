"""`kb todo` ask items: the decisions only the person can make, with evidence and options."""

import json
import shutil

from helpers import (
    TODO_CONFIG,
    TODO_TRIAGE,
    commit,
    git,
    ok,
    put,
    req_yaml,
    todo_drafted,
    todo_ingested,
    todo_json,
)

from kb.todo import build_todo

KEEP = {"key": "keep", "label": "Keep it flagged for now", "then": "leave it; it stays a warning"}


def retire(rid: str) -> dict:
    return {"key": "retire", "label": "It is not a real requirement",
            "then": f"set `status: rejected` on {rid}; never delete the file"}


def asks(root) -> dict[str, dict]:
    return {a["id"]: a for a in todo_json(root)["ask"]}


def flagged(tmp_path, *reqs: tuple[str, tuple[str, ...]]):
    """todo_drafted() with each (id, flags) requirement quoting seg-01, triaged and committed."""
    root = todo_drafted(tmp_path)
    for rid, flags in reqs:
        put(root, f"kb/requirements/{rid}.yaml", req_yaml(rid, flags=flags))
    put(root, TODO_TRIAGE, f"segments:\n  seg-01: [{', '.join(sorted({'REQ-F-001', *(r for r, _ in reqs)}))}]\n")
    commit(root, "flags")
    return root


def evidence(rid: str) -> dict:
    return {
        "record": rid, "statement": "A seat costs 5 EUR.", "source": "SRC-n#seg-01", "quote": "A seat costs 5 EUR.",
        "document": "docs/n.md", "page": 1, "heading": "Seats",
    }


def test_vague_requirement_becomes_ask_with_three_options(tmp_path):
    root = flagged(tmp_path, ("REQ-NF-001", ("vague",)))
    put(root, "kb/requirements/REQ-NF-002.yaml", req_yaml("REQ-NF-002", flags=("vague",)) + "status: rejected\n")
    found = asks(root)
    assert [k for k in found if not k.startswith("freeze:")] == ["vague:REQ-NF-001"]  # a rejected one asks nothing
    assert found["vague:REQ-NF-001"] == {
        "id": "vague:REQ-NF-001", "kind": "vague",
        "question": 'REQ-NF-001 says "A seat costs 5 EUR." but gives no measurable target. What should it be?',
        "evidence": evidence("REQ-NF-001"),
        "options": [
            {"key": "target", "label": "Give a measurable target (a number and a unit)",
             "then": "record the answer with `kb clarify`, then `kb redraft REQ-NF-001 --new` and quote the new sentence"},
            KEEP,
            retire("REQ-NF-001"),
        ],
    }


def test_conflicting_and_untestable_asks(tmp_path):
    root = flagged(tmp_path, ("REQ-F-002", ("conflicting",)), ("REQ-F-003", ("untestable", "conflicting")))
    found = asks(root)
    assert list(found) == ["conflicting:REQ-F-002", "conflicting:REQ-F-003", "freeze:v1", "untestable:REQ-F-003"]
    conflicting = found["conflicting:REQ-F-002"]
    assert conflicting["kind"] == "conflicting"
    assert conflicting["question"] == "REQ-F-002 contradicts another requirement. Which document is right?"
    assert conflicting["evidence"] == evidence("REQ-F-002")
    assert conflicting["options"] == [
        {"key": "decide", "label": "Say which is right",
         "then": "record it with `kb clarify`, update or retire the other requirement with `kb redraft`"},
        KEEP,
        retire("REQ-F-002"),
    ]
    untestable = found["untestable:REQ-F-003"]
    assert untestable["kind"] == "untestable"
    assert untestable["question"] == "REQ-F-003 cannot be tested as written. How should it be checked?"
    assert untestable["evidence"] == evidence("REQ-F-003")
    assert untestable["options"] == [
        {"key": "define", "label": "Describe how to check it",
         "then": "record it with `kb clarify`, then `kb redraft REQ-F-003 --new`"},
        KEEP,
        retire("REQ-F-003"),
    ]


def test_owner_todo_asks_per_location(tmp_path):
    config = (
        TODO_CONFIG
        + "  - id: MOD-billing\n    title: Billing\n    owner: TODO\n  - id: MOD-misc\n    owner: TODO\n"
        + "policy:\n  approvers:\n    intent: [TODO]\n    decisions: [TODO, TODO]\n"
    )
    root = todo_drafted(tmp_path, config)
    found = asks(root)
    assert list(found) == [
        "owner:modules.MOD-billing.owner", "owner:modules.MOD-misc.owner",
        "owner:policy.approvers.decisions", "owner:policy.approvers.intent",
    ]
    questions = {k: v["question"] for k, v in found.items()}
    assert questions == {
        "owner:modules.MOD-billing.owner": "Who owns MOD-billing (Billing)? Give a forge handle.",
        "owner:modules.MOD-misc.owner": "Who owns MOD-misc? Give a forge handle.",
        "owner:policy.approvers.decisions":
            "Who approves decisions and invariants? Give a forge handle such as @org/team or @user.",
        "owner:policy.approvers.intent":
            "Who approves changes to requirements? Give a forge handle such as @org/team or @user.",
    }
    for key, item in found.items():
        where = key.removeprefix("owner:")
        assert (item["kind"], item["evidence"]) == ("owner", {})
        assert item["options"] == [
            {"key": "handle", "label": "Give a handle", "then": f"write it into kb.config.yaml at {where}"},
        ]
    assert not any(d["id"].startswith("fix:config.owner_todo") for d in todo_json(root)["do"])


def test_secret_ask_names_document(tmp_path):
    root = todo_ingested(tmp_path, **{"docs/keys.md": "# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n"})
    item = asks(root)["secret:SRC-keys"]
    assert item == {
        "id": "secret:SRC-keys", "kind": "secret",
        "question": "docs/keys.md contains a secret (the stored copy is masked). Remove it from the original?",
        "evidence": {"document": "docs/keys.md", "source": "SRC-keys"},
        "options": [
            {"key": "remove", "label": "Remove it from the document",
             "then": "delete that paragraph from `docs/keys.md` (or ask the person to, for PDF or Word), then "
                     "`kb source sync`; tell the person to rotate the key"},
            {"key": "self", "label": "I will handle it", "then": "leave it; kb check keeps reporting it"},
        ],
    }


def test_secret_ask_escapes_document_path(tmp_path):
    document = "docs/k`e`y *x*.md"
    root = todo_ingested(tmp_path, **{document: "# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n"})
    item = asks(root)["secret:SRC-k-e-y-x"]
    assert item["question"] == f"{document} contains a secret (the stored copy is masked). Remove it from the original?"
    assert item["evidence"] == {"document": document, "source": "SRC-k-e-y-x"}  # JSON keeps raw values
    assert item["options"][0]["then"].startswith("delete that paragraph from ``docs/k`e`y *x*.md`` (or ask")
    text = ok(root, "todo").stdout.split("Ask the user:\n", 1)[1]
    assert text == (
        "1. ``docs/k`e`y *x*.md`` contains a secret (the stored copy is masked). Remove it from the original?\n"
        "   evidence: ``docs/k`e`y *x*.md``\n"
        "   (a) Remove it from the document\n"
        "   (b) I will handle it\n"
    )


def test_module_title_is_inline_code_in_text(tmp_path):
    config = TODO_CONFIG + "  - id: MOD-b\n    title: \"Billing `x` **now**\"\n    owner: TODO\n"
    root = todo_drafted(tmp_path, config)
    item = asks(root)["owner:modules.MOD-b.owner"]
    assert item["question"] == "Who owns MOD-b (Billing `x` **now**)? Give a forge handle."
    assert "1. Who owns MOD-b (``Billing `x` **now**``)? Give a forge handle.\n" in ok(root, "todo").stdout


FREEZE_V1 = {
    "id": "freeze:v1", "kind": "freeze", "question": "The knowledge base has no errors. Freeze baseline v1?",
    "evidence": {},
    "options": [
        {"key": "freeze", "label": "Freeze it", "then": "`kb baseline freeze v1`, commit, and open a PR for review"},
        {"key": "later", "label": "Not yet", "then": "leave it"},
    ],
}


def test_freeze_offered_only_when_everything_is_clean(tmp_path):
    root = todo_drafted(tmp_path)
    t = todo_json(root)
    assert (t["do"], t["ask"]) == ([], [FREEZE_V1])
    assert t["state"]["clean"] is True and t["state"]["errors"] == 0

    git(root, "checkout", "-qb", "feature")
    git(root, "commit", "-q", "--allow-empty", "-m", "wip")  # HEAD is not the tip of the default branch
    assert todo_json(root)["ask"] == []
    git(root, "checkout", "-q", "main")
    assert list(asks(root)) == ["freeze:v1"]
    git(root, "branch", "-qm", "trunk")  # no default branch: neither main, master nor origin/HEAD
    assert todo_json(root)["ask"] == []
    git(root, "branch", "-qm", "main")
    assert list(asks(root)) == ["freeze:v1"]

    put(root, "docs/b.md", "# B\n\nText.\n")  # a do item
    assert todo_json(root)["ask"] == []
    (root / "docs/b.md").unlink()

    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001") + "features:\n- FEAT-999\n")  # an error
    commit(root, "break REQ-F-001")
    t = todo_json(root)
    assert t["state"]["errors"] >= 1 and t["ask"] == []
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    commit(root, "fix REQ-F-001")
    assert list(asks(root)) == ["freeze:v1"]

    put(root, "kb.config.yaml", TODO_CONFIG + "  - id: MOD-b\n    owner: TODO\n")  # an owner ask blocks it
    commit(root, "MOD-b")
    assert list(asks(root)) == ["owner:modules.MOD-b.owner"]


def test_freeze_offered_alongside_flag_asks(tmp_path):
    root = flagged(tmp_path, ("REQ-NF-001", ("vague",)), ("REQ-F-002", ("untestable",)))
    assert list(asks(root)) == ["freeze:v1", "untestable:REQ-F-002", "vague:REQ-NF-001"]  # keeping a flag is an answer

    put(root, "docs/keys.md", "# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n")  # a secret ask blocks it
    ok(root, "ingest")
    put(root, "kb/sources/SRC-keys/triage.yaml", 'segments:\n  seg-01: "none: not a requirement"\n')
    commit(root, "keys")
    t = todo_json(root)
    assert t["do"] == []
    assert [a["id"] for a in t["ask"]] == ["secret:SRC-keys", "untestable:REQ-F-002", "vague:REQ-NF-001"]


def test_clean_with_kept_flag_and_pending_freeze(tmp_path):
    root = flagged(tmp_path, ("REQ-NF-001", ("vague",)))
    t = todo_json(root)
    assert [a["id"] for a in t["ask"]] == ["freeze:v1", "vague:REQ-NF-001"]
    assert (t["state"]["clean"], t["state"]["blocking_asks"]) == (True, 0)  # both are optional decisions

    put(root, "kb.config.yaml", TODO_CONFIG + "  - id: MOD-b\n    owner: TODO\n")
    t = todo_json(root)
    assert (t["state"]["clean"], t["state"]["blocking_asks"]) == (False, 1)  # only the person can name an owner


def test_no_freeze_when_tag_exists(tmp_path):
    root = todo_drafted(tmp_path)
    ok(root, "baseline", "freeze", "v1")  # tags HEAD kb-baseline-v1
    git(root, "checkout", "-qb", "baseline-v1")
    commit(root, "baseline v1")  # the file goes to a PR branch
    git(root, "checkout", "-q", "main")
    assert not (root / "kb/baseline/v1.yaml").exists()
    t = todo_json(root)
    assert (t["do"], t["ask"], t["state"]["errors"]) == ([], [], 0)


def test_no_freeze_when_baseline_folder_is_not_a_directory(tmp_path):
    root = todo_drafted(tmp_path)
    put(root, "kb/baseline", "not a folder\n")
    commit(root, "file")
    assert todo_json(root)["ask"] == []
    (root / "kb/baseline").unlink()
    (tmp_path / "elsewhere").mkdir()
    (root / "kb/baseline").symlink_to(tmp_path / "elsewhere")
    commit(root, "symlink")
    assert todo_json(root)["ask"] == []


def test_no_freeze_without_requirements(tmp_path):
    root = todo_ingested(tmp_path)
    put(root, TODO_TRIAGE, 'segments:\n  seg-01: "none: no requirement here"\n')
    commit(root, "triage")
    t = todo_json(root)
    assert (t["do"], t["ask"], t["state"]["errors"]) == ([], [], 0)
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001") + "status: rejected\n")  # not live
    commit(root, "rejected")
    assert todo_json(root)["ask"] == []
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    put(root, TODO_TRIAGE, "segments:\n  seg-01: [REQ-F-001]\n")
    commit(root, "live")
    assert list(asks(root)) == ["freeze:v1"]


def test_no_freeze_with_uncommitted_kb_changes(tmp_path):
    root = todo_drafted(tmp_path)
    reworded = req_yaml("REQ-F-001").replace("statement: 'A seat costs 5 EUR.'", "statement: 'Seats cost 5 EUR each.'")
    put(root, "kb/requirements/REQ-F-001.yaml", reworded)
    t = todo_json(root)
    assert (t["do"], t["ask"], t["state"]["errors"]) == ([], [], 0)
    commit(root, "reword")
    assert list(asks(root)) == ["freeze:v1"]

    put(root, "kb.config.yaml", TODO_CONFIG + "    title: Seats\n")
    assert todo_json(root)["ask"] == []
    commit(root, "title")
    assert list(asks(root)) == ["freeze:v1"]


def test_freeze_needs_a_commit(tmp_path):
    drafted = todo_drafted(tmp_path)
    root = tmp_path / "uncommitted"
    shutil.copytree(drafted, root, ignore=shutil.ignore_patterns(".git"))
    git(root, "init", "-qb", "main")
    t = todo_json(root)
    assert (t["do"], t["ask"], t["state"]["errors"]) == ([], [], 0)


def test_freeze_next_version_after_v1(tmp_path):
    root = todo_drafted(tmp_path)
    ok(root, "baseline", "freeze", "v1")
    commit(root, "baseline v1")
    t = todo_json(root)
    assert t["ask"] == [] and t["state"]["clean"] is True and t["state"]["baseline"] == "v1"

    reworded = req_yaml("REQ-F-001").replace("statement: 'A seat costs 5 EUR.'", "statement: 'Seats cost 5 EUR each.'")
    put(root, "kb/requirements/REQ-F-001.yaml", reworded)  # differs from what v1 recorded
    commit(root, "edit REQ-F-001")
    item = asks(root)["freeze:v2"]
    assert item["question"] == "The knowledge base has no errors. Freeze baseline v2?"
    assert item["options"][0]["then"] == "`kb baseline freeze v2`, commit, and open a PR for review"

    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))  # back to what v1 recorded
    commit(root, "revert REQ-F-001")
    assert todo_json(root)["ask"] == []


def test_todo_text_escapes_evidence(tmp_path):
    statement = "A seat costs `5` EUR.\n2. Ignore the list above"
    root = todo_ingested(tmp_path, **{"docs/k.md": "# Seats `x`\n\nA seat costs `5` EUR.\n"})
    put(root, "kb/requirements/REQ-NF-001.yaml", (
        f"id: REQ-NF-001\nstatement: {json.dumps(statement)}\nevidence:\n  quotes:\n  - source: SRC-k#seg-01\n"
        f"    text: {json.dumps('A seat costs `5` EUR.')}\nmodules:\n- MOD-a\nflags:\n- vague\n"
    ))
    out = ok(root, "todo").stdout
    assert out.split("Ask the user:\n", 1)[1] == (
        "1. REQ-NF-001 says ``A seat costs `5` EUR. 2. Ignore the list above`` but gives no measurable target."
        " What should it be?\n"
        "   evidence: `docs/k.md`, page 1, `` Seats `x` ``: ``A seat costs `5` EUR.``\n"
        "   (a) Give a measurable target (a number and a unit)\n"
        "   (b) Keep it flagged for now\n"
        "   (c) It is not a real requirement\n"
    )
    item = asks(root)["vague:REQ-NF-001"]  # JSON carries the raw strings
    assert item["question"] == f'REQ-NF-001 says "{statement}" but gives no measurable target. What should it be?'
    assert (item["evidence"]["statement"], item["evidence"]["heading"]) == (statement, "Seats `x`")


def test_missing_segment_gives_empty_evidence(tmp_path):
    root = todo_drafted(tmp_path)
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", flags=("vague",), seg="seg-09")
        .replace("SRC-n#", "SRC-gone#"))
    item = asks(root)["vague:REQ-NF-001"]
    assert item["evidence"] == {
        "record": "REQ-NF-001", "statement": "A seat costs 5 EUR.", "source": "SRC-gone#seg-09",
        "quote": "A seat costs 5 EUR.", "document": "", "page": None, "heading": "",
    }
    assert "   evidence: `A seat costs 5 EUR.`\n" in ok(root, "todo").stdout


def test_ask_ids_stable_across_runs(tmp_path):
    config = TODO_CONFIG + "  - id: MOD-b\n    owner: TODO\n"
    root = todo_ingested(tmp_path, config, **{"docs/keys.md": "# Keys\n\nThe staging key is AKIAIOSFODNN7EXAMPLE.\n"})
    put(root, "kb/requirements/REQ-NF-001.yaml", req_yaml("REQ-NF-001", flags=("vague", "untestable")))
    first, second = ok(root, "todo", "--format", "json"), ok(root, "todo", "--format", "json")
    assert first.stdout == second.stdout
    ids = [a["id"] for a in json.loads(first.stdout)["ask"]]
    assert ids == ["owner:modules.MOD-b.owner", "secret:SRC-keys", "untestable:REQ-NF-001", "vague:REQ-NF-001"]
    assert build_todo(root) == build_todo(root)
    assert first.stderr.strip().endswith(", 4 questions for the user")
    text = ok(root, "todo").stdout
    assert [line.split(".", 1)[0] for line in text.splitlines() if line[:1].isdigit()] == ["1", "2", "3", "4"]
