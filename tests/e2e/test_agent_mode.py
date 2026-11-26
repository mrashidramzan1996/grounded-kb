"""Agent-driven mode end to end (0.3.0, the evaluation 0.4.0): a scripted "agent" sets up the knowledge base from a
docs-only repository.

The agent runs only the commands the playbook names (`kb todo`, the `command` of each `do` item, `kb ingest --report`,
`kb clarify`, `kb redraft`, `kb redraft --report`, `kb eval judge`, `kb eval report --write`, `kb eval decide`,
`kb check --strict`, `kb baseline freeze`) and writes the files an agent would: drafted records, triage entries, run B
and the verdicts (each in a fresh session that reads only its brief), the person's answers in kb.config.yaml. The
person's answers are fixed strings; kb never supplies them.
"""

import json
import re
import shlex
from pathlib import Path

from helpers import classification, commit, evaluated, git, invoke, make_git_repo, write

from kb.eval.extract import EXTRACT_BRIEF, RUN_B
from kb.eval.judge import JUDGE_BRIEF, VERDICTS
from kb.rules import PLAYBOOK
from kb.yamlio import dump_yaml, load_yaml_strict

SPEC = (
    "# Checkout\n\nCustomers pay by card.\n\n"
    "# Performance\n\nThe checkout page must be fast.\n"
)
REQ = "drafted_by: agent:test via kb {brief} brief v1\nid: {id}\nstatement: {statement}\nevidence:\n  quotes:\n{quotes}modules:\n- MOD-checkout\n{fields}{flags}"
TARGET = "The checkout page loads in under 2 seconds."


def req(rid: str, statement: str, quotes: list[tuple[str, str]], brief: str = "ingest", flags: str = "") -> str:
    lines = "".join(f"  - source: {src}\n    text: {text}\n" for src, text in quotes)
    fields = "".join(f"{k}: {v}\n" for k, v in classification(rid).items())
    return REQ.format(brief=brief, id=rid, statement=statement, quotes=lines, flags=flags, fields=fields)


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
              f"  {s}:\n  - REQ-NF-001\n" if s == seg else f"  {s}: 'none: heading: heading of the clarifications file'\n"
              for s in sorted(re.findall(r"### SRC-clarifications#(seg-\d+)", brief))
          ))
    out = kb(repo, "redraft", "--report", "--strict")
    assert "## Redrafted (1)\n\n- REQ-NF-001\n" in out.stdout and "## Rejected (0)" in out.stdout
    assert "## Still flagged (0)" in out.stdout
    commit(repo, "REQ-NF-001: checkout page target")

    # 7. no errors and no blocking asks: the extraction is evaluated (tests/test_todo_eval.py runs that loop; here it
    #    finds nothing), then only the optional freeze is asked
    assert do_ids(todo(repo)) == ["codeowners"]  # the approvers are set: CODEOWNERS routing is switched on first
    kb(repo, "codeowners", "sync")
    commit(repo, "CODEOWNERS: approvers")
    assert do_ids(todo(repo)) == ["eval:extract"]
    evaluated(repo)
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
    assert do_ids(t) == ["branch", "ingest:docs/refund-policy.md"]  # on main after the baseline: a branch first
    branch, item = t["do"]
    assert re.fullmatch(r"git switch -c kb/update-\d{8}", branch["command"])
    assert item["command"] == "kb ingest docs/refund-policy.md" and item["path"] == "docs/refund-policy.md"
    assert t["state"]["clean"] is False

    out = kb(repo, "check")
    assert "warning source.not_ingested" in out.output and "docs/refund-policy.md" in out.output


# --- the playbook, followed to the letter (final review, Critical 1) ---------------------------------------------

SPEC3 = SPEC + "\n# Search\n\nSearch must be quick.\n"
# the playbook sentences the agent acts on; it does nothing (and commits nowhere) the playbook does not say
STEPS = {
    "commit after report": "After `kb ingest --report` or `kb redraft --report` passes, commit the changes",
    "never commit a rejection": "Never commit while `kb ingest --report` or `kb redraft --report` shows a rejection.",
    "owners first": "Answer owner and approver questions first: write the handles into kb.config.yaml and commit that "
                    "file on its own before running any `kb redraft`.",
    "one redraft": "When the user answered several questions, record every answer with `kb clarify` first, then run "
                   "one `kb redraft <ID> <ID> … --new` for all of them.",
    "by": "`--by` is the handle of the person who answered.",
    "stop": "Repeat until there are no `do` items and no ask you have not yet put to the user in this session, then "
            "run `kb check --strict`.",
    "file tools": "Write each record file with your file-editing tool; do not write or run scripts that generate records.",
    "branch": "After it, make changes on a branch and open a pull request; never merge it yourself. `kb todo` puts a "
              "`branch` item first when you must start that branch: run its command before any other `do` item.",
    "evaluate": "Before freezing a baseline, and after adding or changing documents, evaluate the extraction: when "
                "`kb todo` lists `eval:` items, follow them in order; do the blind extraction in a fresh session or "
                "subagent that does not read the existing records.",
    "judge": "Judge each evaluation item on its own and over-flag; never decide a finding yourself — ask the user, then "
             "record their answer with `kb eval decide --by <their handle>`.",
}
REFUND = "# Refunds\n\nRefunds are paid within 14 days.\n"
SEARCH_TARGET = "Search answers in under 1 second."
USER = "@acme/product"  # who answers, as the person says in their reply
MAX_ROUNDS = 14
# the scripted judge over-flags this record: its statement gives a number the document's line does not
OVERFLAGGED = "REQ-NF-002"


def _sections(brief: str, heading: str) -> list[str]:
    """The `### ` sections of the brief whose heading line starts with `heading`, each up to the next one (the last up
    to `## Remember`); document text inside them may hold headings of its own."""
    starts = [m.start() for m in re.finditer(rf"^### {re.escape(heading)}", brief, re.MULTILINE)]
    ends = [*starts[1:], brief.index("\n## Remember\n")]
    return [brief[a:b] for a, b in zip(starts, ends)]


def _fenced(section: str, info: str) -> str:
    """The text of the section's block fenced with info string `info` (its fence is a run of three or more backticks)."""
    m = re.search(r"^(`{3,})" + info + r"\n", section, re.MULTILINE)
    end = section.index(f"\n{m.group(1)}\n", m.end() - 1)
    return section[m.end():end]


class BlindExtractor:
    """A fresh session following .kb/cache/eval/extract.md: it reads that brief and nothing else, and writes run B
    under .kb/cache/eval/run-b/, one requirement per sentence of every segment."""

    def __init__(self, repo: Path):
        self.repo = repo

    def follow(self) -> None:
        brief = (self.repo / EXTRACT_BRIEF).read_text()
        n = 0
        for section in _sections(brief, "SRC-"):
            ref, body = section.split(" ")[1], _fenced(section, "source")
            text = "\n".join(line for line in body.split("\n")[1:] if not line.startswith("#"))  # not the heading
            for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
                if len(sentence.replace(" ", "")) < 12:
                    continue
                n += 1
                write(self.repo, f"{RUN_B}/requirements/B-REQ-F-{n:03d}.yaml",
                      f"id: B-REQ-F-{n:03d}\nstatement: {json.dumps(sentence)}\nevidence:\n  quotes:\n  - source: {ref}\n"
                      f"    text: {json.dumps(sentence)}\nmodules: [MOD-checkout]\n")
        assert n, "the brief shows no segment"


class Judge:
    """A fresh session following .kb/cache/eval/judge.md: it judges each item on its own from the brief alone and
    writes .kb/cache/eval/verdicts.yaml. It over-flags OVERFLAGGED, and finds no rule or entity missing."""

    def __init__(self, repo: Path):
        self.repo = repo

    def follow(self) -> None:
        brief = (self.repo / JUDGE_BRIEF).read_text()
        out = []
        for section in _sections(brief, "`F-"):
            fid = re.match(r"### `(F-[0-9a-f]{10})`", section).group(1)
            question = re.search(r"^Question: (.*)$", section, re.MULTILINE).group(1)
            text = _fenced(section, "text")
            context = _fenced(section[section.index("\nContext:\n"):], "text")
            v: dict = {"id": fid, "span": text.split("\n")[0]}
            if question == "Record":
                added = "no" if f"requirement {OVERFLAGGED}" in context else "yes"
                v |= {"same_meaning": "yes", "same_actor": "yes", "same_modality": "yes", "no_added_constraint": added}
                if added == "no":
                    v["note"] = "the statement gives a target the first passage does not"
            elif question == "Possible rule":
                v |= {"verdict": "not_a_rule"}
            elif question == "Record granularity":
                v |= {"verdict": "ok"}
            elif question == "Entity":
                v |= {"definition_matches_quote": "yes", "kind_correct": "yes"}
            elif question == "Relation":
                v |= {"quote_states_relation": "yes", "direction_correct": "yes"}
            else:  # a possible entity or relation
                v |= {"verdict": "not_an_entity"}
            out.append(v)
        count = int(re.search(r"^## Items \((\d+)\)$", brief, re.MULTILINE).group(1))
        assert len(out) == count, (len(out), count)
        (self.repo / VERDICTS).write_text(dump_yaml({"verdicts": out}))


class LiteralAgent:
    """Runs only what the playbook (src/kb/rules.py PLAYBOOK) names, and commits only where it says to:
    step 3 (after a passing report), step 11 (kb.config.yaml on its own), the `commit` item and the freeze option's
    `then`; after the baseline it starts the branch the `branch` item names (step 16). It follows the `eval:` items in
    order, with a fresh session for the blind extraction and another for the judge (steps 5 and 6), and records the
    person's decisions on what they find with `kb eval decide`. Drafting and redrafting stand in for the agent
    following the brief, writing each record file directly (step 2); the person's answers are fixed strings."""

    def __init__(self, repo: Path):
        playbook = "\n".join(PLAYBOOK)
        missing = [name for name, sentence in STEPS.items() if sentence not in playbook]
        assert not missing, f"the playbook does not say: {missing}"
        self.repo = repo
        self.asked: set[str] = set()  # step 9: never ask the same thing twice in a session
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
        if item["id"] == "eval:run-b":  # step 5: the blind extraction, in a fresh session; then what the item says
            assert item["message"] == ("follow .kb/cache/eval/extract.md in a fresh session or subagent, then run "
                                       "`kb eval judge`") and item["command"] is None
            BlindExtractor(self.repo).follow()
            kb(self.repo, "eval", "judge")
            return
        if item["id"] == "eval:verdicts":  # step 6: each item judged on its own, in a fresh session
            assert item["message"] == "follow .kb/cache/eval/judge.md, then run `kb eval report --write`"
            Judge(self.repo).follow()
            kb(self.repo, "eval", "report", "--write")
            return
        assert item["command"], item
        argv = shlex.split(item["command"])
        if item["id"] == "branch":  # step 16: the work goes on a new branch, for a pull request
            assert argv[:3] == ["git", "switch", "-c"] and len(argv) == 4, item
            git(self.repo, *argv[1:])
            self.pull_requests.append(argv[3])
            return
        assert argv[0] == "kb", item
        kb(self.repo, *argv[1:])
        if item["id"] == "ingest:all":
            self.follow_ingest_brief()
            self.report("ingest")
        elif item["id"] == "ingest:docs/refund-policy.md":
            self.follow_refund_brief()
            self.report("ingest")
        elif item["id"].startswith("report:"):
            self.report(item["id"].split(":")[1])
        else:
            assert item["id"] in ("init", "eval:extract", "eval:judge", "codeowners"), item

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

    def follow_refund_brief(self) -> None:
        brief = (self.repo / ".kb/cache/ingest/brief.md").read_text()
        assert "### SRC-refund-policy#seg-01" in brief
        text = "Refunds are paid within 14 days."
        write(self.repo, "kb/requirements/REQ-F-002.yaml", req("REQ-F-002", text, [("SRC-refund-policy#seg-01", text)]))
        write(self.repo, "kb/sources/SRC-refund-policy/triage.yaml", "segments:\n  seg-01:\n  - REQ-F-002\n")

    # -- steps 8 to 14: the asks ------------------------------------------------------------------------------

    def ask(self, asks: list[dict], reply: dict[str, tuple[str, str]]) -> None:
        """Ask every item in one message; `reply` is the person's answer: {ask id: (option key, the fact)}."""
        assert sorted(reply) == sorted(a["id"] for a in asks), "the person answers every question asked"
        self.asked |= set(reply)
        by_id = {a["id"]: a for a in asks}
        # step 11: owner and approver answers first, committed on their own
        owners = [i for i in reply if by_id[i]["kind"] == "owner"]
        for ask_id in owners:
            key, handle = reply[ask_id]
            assert key == "handle"
            answer_owner(self.repo, {"ask": asks}, ask_id.removeprefix("owner:"), handle)
        if owners:
            self.commit("kb.config.yaml: owners and approvers", "kb.config.yaml")
        # steps 12 to 14: record every fact with kb clarify, then one redraft for all of them
        refs = {}
        for ask_id in sorted(set(reply) - set(owners)):
            key, fact = reply[ask_id]
            option = next(o for o in by_id[ask_id]["options"] if o["key"] == key)
            if ask_id.startswith("freeze:"):
                assert key == "freeze" and "never merge it yourself" in option["then"]
                self.freeze(ask_id.removeprefix("freeze:"))
                continue
            if by_id[ask_id]["kind"] in ("categories", "dismissed", "unfaithful"):  # step 6: the person's answer, by its handle
                self.decide(option["then"], fact)
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
            f"  {s}:\n  - {cited[s]}\n" if s in cited else f"  {s}: 'none: heading: heading of the clarifications file'\n"
            for s in segs
        ))

    def decide(self, then: str, reason: str) -> None:
        """Run the `kb eval decide` command the option's `then` names, with the person's handle and reason."""
        (command,) = re.fullmatch(r"`(kb eval decide [^`]*)`", then).groups()
        argv = shlex.split(command.replace("<handle>", USER))
        argv = [reason if a == "<why>" else a for a in argv]
        assert "<" not in " ".join(argv), argv
        kb(self.repo, *argv[1:])

    def freeze(self, version: str) -> None:
        """The freeze option's `then`: freeze, commit the file on a new branch and open a pull request."""
        kb(self.repo, "baseline", "freeze", version)
        git(self.repo, "checkout", "-qb", f"baseline-{version}")
        self.commit(f"kb baseline {version}", f"kb/baseline/{version}.yaml")
        self.pull_requests.append(f"baseline-{version}")


def session(repo: Path, agent: LiteralAgent, replies: list) -> tuple[int, dict]:
    """Steps 1 and 15: run `kb todo` and work through it until nothing is left; (rounds, the first kb todo). Each reply
    is the person's answer to the next message of questions: a dict, or a function of the questions asked."""
    rounds, first = 0, None
    while True:
        rounds += 1
        assert rounds <= MAX_ROUNDS, f"the agent loops: {agent.commits}"
        t = todo(repo)
        first = first or t
        pending = [a for a in t["ask"] if a["id"] not in agent.asked]
        if not t["do"] and not pending:  # step 15: no do items and no ask not yet put to the user
            break
        for item in t["do"]:
            agent.do(item)
        if pending:
            reply = replies.pop(0)
            agent.ask(pending, reply(pending) if callable(reply) else reply)
    kb(repo, "check", "--strict")
    return rounds, first


def evaluation_answers(asks: list[dict]) -> dict[str, tuple[str, str]]:
    """The person's answer to the evaluation's questions: no security, privacy or availability requirements apply, they
    agree with what the judge dismissed, and the over-flagged record is correct. They are asked before the freeze: an
    open finding, or a dismissal no one agreed with, blocks it."""
    assert sorted(a["kind"] for a in asks) == ["categories", "dismissed", "unfaithful"]
    (finding,) = (a for a in asks if a["kind"] == "unfaithful")
    assert finding["evidence"]["record"] == OVERFLAGGED and "Search must be quick." in finding["evidence"]["quote"]
    return {"categories": ("none_apply", "An internal checkout with no such needs."), "dismissed": ("agree", ""),
            finding["id"]: ("correct", "")}


def test_agent_follows_playbook_literally(tmp_path):
    repo = make_git_repo(None, tmp_path, extra={"docs/spec.md": SPEC3})
    agent = LiteralAgent(repo)
    replies = [  # what the person says each time the agent asks
        {"owner:policy.approvers.decisions": ("handle", "@acme/architects"),
         "owner:policy.approvers.intent": ("handle", "@acme/product")},
        {"owner:modules.MOD-checkout.owner": ("handle", "@acme/checkout"),  # "1a, 2a, 3a ... I am @acme/product"
         "vague:REQ-NF-001": ("target", TARGET),
         "vague:REQ-NF-002": ("target", SEARCH_TARGET)},
        evaluation_answers,  # the evaluation's questions: "1a none apply, 2a I agree, 3b it is correct"
        {"freeze:v1": ("freeze", "")},
    ]
    rounds, _ = session(repo, agent, replies)

    # four rounds are the evaluation (extract; run B and the judge brief; verdicts and report; its commit, with the
    # person's answers to its questions) and one commits their decisions
    assert replies == [] and rounds == 10
    assert agent.commits == [
        "kb ingest: report passed",
        "kb.config.yaml: owners and approvers",
        "kb.config.yaml: owners and approvers",
        "kb redraft: report passed",
        "kb: commit the knowledge-base changes",  # kb/eval/summary.yaml
        "kb: commit the knowledge-base changes",  # kb/eval/decisions.yaml
        "kb baseline v1",
    ]
    decisions = load_yaml_strict((repo / "kb/eval/decisions.yaml").read_text())["decisions"]
    assert [(d["finding"] if d["finding"] in ("categories", "dismissed") else "F-", d["decision"], d["by"])
            for d in decisions] == [("categories", "not_a_rule", USER), ("dismissed", "ok", USER), ("F-", "correct", USER)]
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

    # a new session: "I added docs/refund-policy.md; update the knowledge base." The work goes on a branch for review
    main = git(repo, "rev-parse", "main").strip()
    write(repo, "docs/refund-policy.md", REFUND)
    agent = LiteralAgent(repo)

    def agree(asks: list[dict]) -> dict[str, tuple[str, str]]:
        """The new evaluation's only question: the judge's dismissals, a new list, so asked again."""
        assert [a["kind"] for a in asks] == ["dismissed"]
        return {"dismissed": ("agree", "")}

    rounds, first = session(repo, agent, [agree])
    assert do_ids(first) == ["branch", "ingest:docs/refund-policy.md"] and first["ask"] == []
    (branch,) = agent.pull_requests
    assert first["do"][0]["command"] == f"git switch -c {branch}" and re.fullmatch(r"kb/update-\d{8}", branch)
    # the new document is evaluated too; the person's earlier decision on the over-flagged record still stands, and
    # they agree with the new judge run's dismissals
    assert rounds == 7 and agent.commits == ["kb ingest: report passed", "kb: commit the knowledge-base changes",
                                             "kb: commit the knowledge-base changes"]
    assert not any(a["id"].startswith(("finding:", "categories", "dismissed")) for a in todo(repo)["ask"])
    assert git(repo, "branch", "--show-current").strip() == branch and git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "main").strip() == main  # main is unchanged: nothing committed there
    assert git(repo, "ls-tree", "--name-only", "main", "kb/requirements/REQ-F-002.yaml", "docs/refund-policy.md") == ""
    assert "Refunds are paid within 14 days." in git(repo, "show", f"{branch}:kb/requirements/REQ-F-002.yaml")
    assert git(repo, "show", f"{branch}:docs/refund-policy.md") == REFUND
    assert todo(repo)["ask"] == []  # no "Freeze baseline v2?" while the work is not reviewed

    # the person merges it: only then is v2 offered, on main
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", branch, "-m", f"Merge {branch}")
    t = todo(repo)
    assert t["do"] == [] and ask_ids(t) == ["freeze:v2"]


def test_approvers_committed_before_the_drafts(tmp_path):
    """What a real agent did: ran `kb ingest`, asked the approver questions straight away, committed kb.config.yaml
    on its own (step 11), then drafted. The ingest base predates the approvers; filling a TODO is no config change."""
    repo = make_git_repo(None, tmp_path, extra={"docs/spec.md": SPEC})
    run_do(repo, todo(repo))
    commit(repo, "kb init")
    t = todo(repo)
    assert do_ids(t) == ["ingest:all"]
    run_do(repo, t)  # kb ingest: the brief records this commit as the base
    answer_owner(repo, t, "policy.approvers.decisions", "@acme/architects")
    answer_owner(repo, t, "policy.approvers.intent", "@acme/product")
    git(repo, "add", "kb.config.yaml")
    git(repo, "commit", "-qm", "kb.config.yaml: approvers")
    kb(repo, "module", "add", "MOD-checkout", "--owner", "TODO", "--title", "Checkout")
    write(repo, "kb/requirements/REQ-F-001.yaml",
          req("REQ-F-001", "Customers pay by card.", [("SRC-spec#seg-01", "Customers pay by card.")]))
    write(repo, "kb/requirements/REQ-NF-001.yaml",
          req("REQ-NF-001", "The checkout page must be fast.", [("SRC-spec#seg-02", "The checkout page must be fast.")],
              flags="flags:\n- vague\n"))
    write(repo, "kb/sources/SRC-spec/triage.yaml", "segments:\n  seg-01:\n  - REQ-F-001\n  seg-02:\n  - REQ-NF-001\n")
    out = kb(repo, "ingest", "--report", "--strict")
    assert "## Rejected (0)" in out.stdout and "ingest.config_changed" not in out.stdout
    assert "## Proposed modules (1)" in out.stdout
