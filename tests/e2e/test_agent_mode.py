"""Agent-driven mode end to end (0.3.0): a scripted "agent" sets up the knowledge base from a docs-only repository.

The agent runs only the commands the playbook names (`kb todo`, the `command` of each `do` item, `kb ingest --report`,
`kb clarify`, `kb redraft`, `kb redraft --report`, `kb check --strict`, `kb baseline freeze`) and writes the files an
agent would: drafted records, triage entries, the person's answers in kb.config.yaml. The person's answers are fixed
strings; kb never supplies them.
"""

import json
import re
import shlex
from pathlib import Path

from helpers import commit, git, invoke, make_git_repo, write

from kb.rules import PLAYBOOK

SPEC = (
    "# Checkout\n\nCustomers pay by card.\n\n"
    "# Performance\n\nThe checkout page must be fast.\n"
)
REQ = "drafted_by: agent:test via kb {brief} brief v1\nid: {id}\nstatement: {statement}\nevidence:\n  quotes:\n{quotes}modules:\n- MOD-checkout\n{flags}"
TARGET = "The checkout page loads in under 2 seconds."


def req(rid: str, statement: str, quotes: list[tuple[str, str]], brief: str = "ingest", flags: str = "") -> str:
    lines = "".join(f"  - source: {src}\n    text: {text}\n" for src, text in quotes)
    return REQ.format(brief=brief, id=rid, statement=statement, quotes=lines, flags=flags)


def kb(repo: Path, *args: str, code: int = 0):
    r = invoke(repo, *args)
    assert r.exit_code == code, r.output
    return r


def todo(repo: Path) -> dict:
    return json.loads(kb(repo, "todo", "--format", "json").stdout)


def do_ids(t: dict) -> list[str]:
    return [d["id"] for d in t["do"]]


def ask_ids(t: dict) -> list[str]:
    return [a["id"] for a in t["ask"]]


def run_do(repo: Path, t: dict) -> None:
    """Run each `do` item's command, as the playbook says."""
    for item in t["do"]:
        assert item["command"], item
        argv = shlex.split(item["command"])
        assert argv[0] == "kb", item
        kb(repo, *argv[1:])


def answer_owner(repo: Path, t: dict, key: str, handle: str) -> None:
    """Follow the owner ask's option: write the handle into kb.config.yaml where it says, then commit it alone."""
    (option,) = next(a for a in t["ask"] if a["id"] == f"owner:{key}")["options"]
    assert option["then"].startswith(f"write it into kb.config.yaml at {key}") and "commit" in option["then"]
    cfg = repo / "kb.config.yaml"
    leaf = key.rsplit(".", 1)[1]
    if key.startswith("modules."):  # modules.<id>.owner
        module = key.split(".")[1]
        text = cfg.read_text()
        start = text.index(f"- id: {module}\n")
        end = text.index("owner: TODO", start)
        cfg.write_text(text[:end] + f'owner: "{handle}"' + text[end + len("owner: TODO"):])
    else:  # policy.approvers.<intent|decisions>
        cfg.write_text(cfg.read_text().replace(f"{leaf}: [TODO]", f'{leaf}: ["{handle}"]', 1))


def set_up_from_docs(tmp_path: Path) -> Path:
    """Steps 1 to 8: from a docs-only repository to a frozen baseline v1, checking kb todo after each step."""
    # 1. a docs-only repository with a vague line
    repo = make_git_repo(None, tmp_path, extra={"docs/spec.md": SPEC})

    # 2. nothing is set up yet
    t = todo(repo)
    assert do_ids(t) == ["init"] and t["ask"] == [] and t["state"]["initialized"] is False

    # 3. kb init, committed: ingest everything; the approvers are the person's to name
    run_do(repo, t)
    commit(repo, "kb init")
    t = todo(repo)
    assert do_ids(t) == ["ingest:all"]
    assert ask_ids(t) == ["owner:policy.approvers.decisions", "owner:policy.approvers.intent"]
    assert t["state"]["clean"] is False and t["state"]["blocking_asks"] == 2

    # 4. kb ingest; the agent follows the brief: one module (no owner in the documents), two requirements, one of
    #    them flagged vague, every segment triaged
    run_do(repo, t)
    brief = (repo / ".kb/cache/ingest/brief.md").read_text()
    assert "### SRC-spec#seg-01" in brief and "### SRC-spec#seg-02" in brief and "The checkout page must be fast." in brief
    kb(repo, "module", "add", "MOD-checkout", "--owner", "TODO", "--title", "Checkout")
    write(repo, "kb/requirements/REQ-F-001.yaml",
          req("REQ-F-001", "Customers pay by card.", [("SRC-spec#seg-01", "Customers pay by card.")]))
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          req("REQ-NF-001", "The checkout page must be fast.", [("SRC-spec#seg-02", "The checkout page must be fast.")],
              flags="flags:\n- vague\n"))
    write(repo, "kb/sources/SRC-spec/triage.yaml", "segments:\n  seg-01:\n  - REQ-F-001\n  seg-02:\n  - REQ-NF-001\n")
    out = kb(repo, "ingest", "--report", "--strict")
    assert "## Accepted (2)\n\n- REQ-F-001\n- REQ-NF-001\n" in out.stdout and "## Rejected (0)" in out.stdout
    commit(repo, "drafts")

    # 5. nothing left to do; four questions: the vague line, the module owner and both approvers
    t = todo(repo)
    assert t["do"] == []
    assert ask_ids(t) == [
        "owner:modules.MOD-checkout.owner", "owner:policy.approvers.decisions", "owner:policy.approvers.intent",
        "vague:REQ-NF-001",
    ]
    vague = next(a for a in t["ask"] if a["id"] == "vague:REQ-NF-001")
    assert vague["evidence"]["quote"] == "The checkout page must be fast." and vague["evidence"]["source"] == "SRC-spec#seg-02"
    assert [o["key"] for o in vague["options"]] == ["target", "keep", "retire"]
    text = kb(repo, "todo").stdout
    assert "REQ-NF-001 says `The checkout page must be fast.` but gives no measurable target." in text

    # 6. the person answers "1a @acme/checkout, 2a @acme/architects, 3a @acme/product, 4a under 2 seconds"
    answer_owner(repo, t, "modules.MOD-checkout.owner", "@acme/checkout")
    answer_owner(repo, t, "policy.approvers.decisions", "@acme/architects")
    answer_owner(repo, t, "policy.approvers.intent", "@acme/product")
    commit(repo, "owners and approvers")
    out = kb(repo, "clarify", "--topic", "Checkout page load target", "--text", TARGET, "--by", "@acme/product",
             "--date", "2026-11-25")
    ref = re.fullmatch(r"recorded in docs/clarifications\.md; quote it as (SRC-clarifications#seg-\d+)\n", out.stdout)
    assert ref, out.stdout
    ref = ref.group(1)
    kb(repo, "redraft", "REQ-NF-001", "--new")
    brief = (repo / ".kb/cache/redraft/brief.md").read_text()
    assert "## REQ-NF-001" in brief and f"### {ref}" in brief and TARGET in brief
    # the agent redrafts REQ-NF-001 with the recorded target, drops `vague`, and triages the clarifications
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          req("REQ-NF-001", TARGET, [("SRC-spec#seg-02", "The checkout page must be fast."), (ref, TARGET)], brief="redraft"))
    seg = ref.split("#")[1]
    write(repo, "kb/sources/SRC-clarifications/triage.yaml",
          "segments:\n" + "".join(
              f"  {s}:\n  - REQ-NF-001\n" if s == seg else f"  {s}: 'none: heading of the clarifications file'\n"
              for s in sorted(re.findall(r"### SRC-clarifications#(seg-\d+)", brief))
          ))
    out = kb(repo, "redraft", "--report", "--strict")
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout and "## Rejected (0)" in out.stdout
    assert "## Still flagged (0)" in out.stdout
    commit(repo, "REQ-NF-001: checkout page target")

    # 7. no errors, nothing to do, no blocking asks: only the optional freeze is asked
    t = todo(repo)
    assert t["do"] == [] and ask_ids(t) == ["freeze:v1"]
    assert t["state"]["clean"] is True and t["state"]["errors"] == 0 and t["state"]["baseline"] is None
    kb(repo, "check", "--strict")

    # 8. the person says freeze: kb baseline freeze v1, committed
    kb(repo, "baseline", "freeze", "v1")
    commit(repo, "baseline v1")
    return repo


def test_agent_driven_setup_from_docs(tmp_path):
    repo = set_up_from_docs(tmp_path)
    t = todo(repo)
    assert t["do"] == [] and t["ask"] == []
    assert t["state"]["clean"] is True and t["state"]["baseline"] == "v1"
    assert (repo / "kb/baseline/v1.yaml").is_file()
    assert "Decided by @acme/product on 2026-11-25." in (repo / "docs/clarifications.md").read_text()
    kb(repo, "check", "--strict")


def test_new_document_after_freeze_is_detected(tmp_path):
    repo = set_up_from_docs(tmp_path)
    write(repo, "docs/refund-policy.md", "# Refunds\n\nRefunds are paid within 14 days.\n")

    t = todo(repo)
    assert do_ids(t) == ["ingest:docs/refund-policy.md"]
    (item,) = t["do"]
    assert item["command"] == "kb ingest docs/refund-policy.md" and item["path"] == "docs/refund-policy.md"
    assert t["state"]["clean"] is False

    out = kb(repo, "check")
    assert "warning source.not_ingested" in out.output and "docs/refund-policy.md" in out.output


# --- the playbook, followed to the letter (final review, Critical 1) ---------------------------------------------

SPEC3 = SPEC + "\n# Search\n\nSearch must be quick.\n"
# the playbook sentences the agent acts on; it does nothing (and commits nowhere) the playbook does not say
STEPS = {
    "commit after report": "After `kb ingest --report` or `kb redraft --report` passes, commit the changes",
    "owners first": "Answer owner and approver questions first: write the handles into kb.config.yaml and commit that "
                    "file on its own before running any `kb redraft`.",
    "one redraft": "When the user answered several questions, record every answer with `kb clarify` first, then run "
                   "one `kb redraft <ID> <ID> … --new` for all of them.",
    "by": "`--by` is the handle of the person who answered.",
    "stop": "Repeat until there are no `do` items and no ask you have not yet put to the user in this session, then "
            "run `kb check --strict`.",
}
SEARCH_TARGET = "Search answers in under 1 second."
USER = "@acme/product"  # who answers, as the person says in their reply
MAX_ROUNDS = 8


class LiteralAgent:
    """Runs only what the playbook (src/kb/rules.py PLAYBOOK) names, and commits only where it says to:
    step 3 (after a passing report), step 7 (kb.config.yaml on its own) and the freeze option's `then`. Drafting
    and redrafting stand in for the agent following the brief; the person's answers are fixed strings."""

    def __init__(self, repo: Path):
        playbook = "\n".join(PLAYBOOK)
        missing = [name for name, sentence in STEPS.items() if sentence not in playbook]
        assert not missing, f"the playbook does not say: {missing}"
        self.repo = repo
        self.asked: set[str] = set()  # step 5: never ask the same thing twice in a session
        self.commits: list[str] = []
        self.pull_requests: list[str] = []

    def commit(self, message: str, *paths: str) -> None:
        git(self.repo, "add", *(paths or ("-A",)))
        git(self.repo, "commit", "-qm", message)
        self.commits.append(message)

    def report(self, flow: str) -> None:
        """Step 2: run the report; step 3: commit once it passes. Every report must pass."""
        out = kb(self.repo, flow, "--report", "--strict").stdout
        assert "## Rejected (0)" in out, out
        assert f"report:{flow}" in do_ids(todo(self.repo))  # kb todo, too, says the result is not committed yet
        self.commit(f"kb {flow}: report passed")

    # -- step 2: the do items ---------------------------------------------------------------------------------

    def do(self, item: dict) -> None:
        if item["id"] == "commit":
            self.commit("kb: commit the knowledge-base changes")
            return
        assert item["command"], item
        argv = shlex.split(item["command"])
        assert argv[0] == "kb", item
        kb(self.repo, *argv[1:])
        if item["id"] == "ingest:all":
            self.follow_ingest_brief()
            self.report("ingest")
        elif item["id"].startswith("report:"):
            self.report(item["id"].split(":")[1])
        else:
            assert item["id"] == "init", item

    def follow_ingest_brief(self) -> None:
        brief = (self.repo / ".kb/cache/ingest/brief.md").read_text()
        for seg in ("seg-01", "seg-02", "seg-03"):
            assert f"### SRC-spec#{seg}" in brief
        kb(self.repo, "module", "add", "MOD-checkout", "--owner", "TODO", "--title", "Checkout")  # no owner in the docs
        vague = "flags:\n- vague\n"
        for rid, text, seg, flags in [
            ("REQ-F-001", "Customers pay by card.", "seg-01", ""),
            ("REQ-NF-001", "The checkout page must be fast.", "seg-02", vague),
            ("REQ-NF-002", "Search must be quick.", "seg-03", vague),
        ]:
            write(self.repo, f"kb/requirements/{rid}.yaml", req(rid, text, [(f"SRC-spec#{seg}", text)], flags=flags))
        write(self.repo, "kb/sources/SRC-spec/triage.yaml",
              "segments:\n  seg-01:\n  - REQ-F-001\n  seg-02:\n  - REQ-NF-001\n  seg-03:\n  - REQ-NF-002\n")

    # -- steps 4 to 10: the asks ------------------------------------------------------------------------------

    def ask(self, asks: list[dict], reply: dict[str, tuple[str, str]]) -> None:
        """Ask every item in one message; `reply` is the person's answer: {ask id: (option key, the fact)}."""
        assert sorted(reply) == sorted(a["id"] for a in asks), "the person answers every question asked"
        self.asked |= set(reply)
        by_id = {a["id"]: a for a in asks}
        # step 7: owner and approver answers first, committed on their own
        owners = [i for i in reply if by_id[i]["kind"] == "owner"]
        for ask_id in owners:
            key, handle = reply[ask_id]
            assert key == "handle"
            answer_owner(self.repo, {"ask": asks}, ask_id.removeprefix("owner:"), handle)
        if owners:
            self.commit("kb.config.yaml: owners and approvers", "kb.config.yaml")
        # step 8 to 10: record every fact with kb clarify, then one redraft for all of them
        refs = {}
        for ask_id in sorted(set(reply) - set(owners)):
            key, fact = reply[ask_id]
            option = next(o for o in by_id[ask_id]["options"] if o["key"] == key)
            if ask_id.startswith("freeze:"):
                assert key == "freeze" and "never merge it yourself" in option["then"]
                self.freeze(ask_id.removeprefix("freeze:"))
                continue
            assert key == "target" and "one `kb redraft" in option["then"] and "then commit" in option["then"]
            out = kb(self.repo, "clarify", "--topic", f"{ask_id} target", "--text", fact, "--by", USER,
                     "--date", "2026-11-25").stdout
            refs[ask_id.removeprefix("vague:")] = re.search(r"SRC-clarifications#seg-\d+", out).group(0)
        if refs:
            kb(self.repo, "redraft", *sorted(refs), "--new")
            self.follow_redraft_brief(refs, {rid: reply[f"vague:{rid}"][1] for rid in refs})
            self.report("redraft")

    def follow_redraft_brief(self, refs: dict[str, str], facts: dict[str, str]) -> None:
        brief = (self.repo / ".kb/cache/redraft/brief.md").read_text()
        quoted = {
            "REQ-NF-001": ("SRC-spec#seg-02", "The checkout page must be fast."),
            "REQ-NF-002": ("SRC-spec#seg-03", "Search must be quick."),
        }
        for rid, ref in refs.items():
            assert f"## {rid}" in brief and f"### {ref}" in brief and facts[rid] in brief
            write(self.repo, f"kb/requirements/{rid}.yaml", req(rid, facts[rid], [quoted[rid], (ref, facts[rid])], brief="redraft"))
        cited = {ref.split("#")[1]: rid for rid, ref in refs.items()}
        segs = sorted(set(re.findall(r"### SRC-clarifications#(seg-\d+)", brief)))
        write(self.repo, "kb/sources/SRC-clarifications/triage.yaml", "segments:\n" + "".join(
            f"  {s}:\n  - {cited[s]}\n" if s in cited else f"  {s}: 'none: heading of the clarifications file'\n"
            for s in segs
        ))

    def freeze(self, version: str) -> None:
        """The freeze option's `then`: freeze, commit the file on a new branch and open a pull request."""
        kb(self.repo, "baseline", "freeze", version)
        git(self.repo, "checkout", "-qb", f"baseline-{version}")
        self.commit(f"kb baseline {version}", f"kb/baseline/{version}.yaml")
        self.pull_requests.append(f"baseline-{version}")


def test_agent_follows_playbook_literally(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"docs/spec.md": SPEC3})
    agent = LiteralAgent(repo)
    replies = [  # what the person says each time the agent asks
        {"owner:policy.approvers.decisions": ("handle", "@acme/architects"),
         "owner:policy.approvers.intent": ("handle", "@acme/product")},
        {"owner:modules.MOD-checkout.owner": ("handle", "@acme/checkout"),  # "1a, 2a, 3a ... I am @acme/product"
         "vague:REQ-NF-001": ("target", TARGET),
         "vague:REQ-NF-002": ("target", SEARCH_TARGET)},
        {"freeze:v1": ("freeze", "")},
    ]
    rounds = 0
    while True:
        rounds += 1
        assert rounds <= MAX_ROUNDS, f"the agent loops: {agent.commits}"
        t = todo(repo)  # steps 1 and 11
        pending = [a for a in t["ask"] if a["id"] not in agent.asked]
        if not t["do"] and not pending:  # step 11: no do items and no ask not yet put to the user
            break
        for item in t["do"]:
            agent.do(item)
        if pending:
            agent.ask(pending, replies.pop(0))
    kb(repo, "check", "--strict")

    assert replies == [] and rounds == 5
    assert agent.commits == [
        "kb ingest: report passed",
        "kb.config.yaml: owners and approvers",
        "kb.config.yaml: owners and approvers",
        "kb redraft: report passed",
        "kb baseline v1",
    ]
    assert agent.pull_requests == ["baseline-v1"]
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "kb/baseline/v1.yaml").is_file()
    clarifications = (repo / "docs/clarifications.md").read_text()
    assert TARGET in clarifications and SEARCH_TARGET in clarifications and "Decided by @acme/product" in clarifications

    # the person (never the agent) merges the pull request: the baseline is in place and nothing is left
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "baseline-v1", "-m", "Merge baseline v1")
    t = todo(repo)
    assert (t["do"], t["ask"]) == ([], [])
    assert t["state"]["clean"] is True and t["state"]["baseline"] == "v1"
