"""`kb todo` stays linear in the open findings: the documents' sentences are split once per repository, not once per
finding (and once per decision)."""

import time

from helpers import TODO_CONFIG, TODO_TRIAGE, commit, make_git_repo, ok, put, req_yaml
from test_todo_eval import evaluate

import kb.sources.sentences as sentences_module
from kb.eval.decisions import finding_sentences, finding_spans, parse_summary, read_summary
from kb.eval.findings import Finding
from kb.records.repo import KbRepo
from kb.sources.sentences import sentence_key, sentences
from kb.todo import build_todo


def many_findings(tmp_path, n: int):
    """A committed KB over one document of `n` rule-like sentences no record quotes, evaluated with one open omission
    finding per sentence."""
    sections = [f"# Section {h}\n\n" + " ".join(f"Rule {h}-{i} must be applied within {i + 1} days." for i in range(10))
                + "\n" for h in range(n // 10)]
    doc = "# Seats\n\nA seat costs 5 EUR.\n\n" + "\n".join(sections)
    root = make_git_repo(None, tmp_path, extra={"kb.config.yaml": TODO_CONFIG, "docs/n.md": doc})
    ok(root, "ingest")
    put(root, "kb/requirements/REQ-F-001.yaml", req_yaml("REQ-F-001"))
    segs = [s.id for s in KbRepo.load(root).segments("SRC-n")]
    put(root, TODO_TRIAGE, "segments:\n" + "".join(
        f"  {s}: [REQ-F-001]\n" if s == "seg-01" else f"  {s}: 'none: other: rules for the test'\n" for s in segs))
    commit(root, "drafts")
    repo = KbRepo.load(root)
    rules = [s for s in sentences(repo, "SRC-n") if s.text.startswith("Rule")]
    evaluate(root, tuple(Finding(f"F-{k:010x}", f"S:{sentence_key(s)}", "omission", s.ref, s.text, "high", 3, (), "",
                                 "requirement") for k, s in enumerate(rules)))
    return root, len(rules)


def test_todo_with_many_open_findings_is_fast(tmp_path):
    # 800 open findings took 12 s before the sentences were split once per repository; now well under one second.
    # The bound is generous: slow CI machines must not flake
    root, n = many_findings(tmp_path, 800)
    assert n == 800
    ok(root, "eval", "decide", "F-0000000000", "--as", "not_a_rule", "--reason", "Not one.", "--by", "@alice")
    commit(root, "one decision")
    getattr(parse_summary, "cache_clear", lambda: None)()  # time a cold read, as one `kb todo` run is
    start = time.perf_counter()
    todo = build_todo(root)
    elapsed = time.perf_counter() - start
    assert sum(a.id.startswith("finding:") for a in todo.ask) == n - 1
    assert elapsed < 5, f"kb todo took {elapsed:.1f}s for {n} open findings"


def test_sentences_are_split_once_per_source_and_repository(tmp_path, monkeypatch):
    root, n = many_findings(tmp_path, 40)
    repo = KbRepo.load(root)
    summary = read_summary(root)
    calls: list[str] = []
    real = sentences_module.segment_sentences

    def counting(text, seg, src_id):
        calls.append(f"{src_id}#{seg.id}")
        return real(text, seg, src_id)

    monkeypatch.setattr(sentences_module, "segment_sentences", counting)
    for f in summary.open_findings:
        assert [s.text for s in finding_sentences(repo, f)] == [f.text]
        assert finding_spans(repo, f)
    assert sentences(repo, "SRC-n") == sentences(repo, "SRC-n")
    segments = [f"SRC-n#{s.id}" for s in repo.segments("SRC-n")]
    assert sorted(calls) == sorted(segments)  # each segment once, for all n findings
    # a new repository splits again: nothing is shared between two loads
    calls.clear()
    sentences(KbRepo.load(root), "SRC-n")
    assert sorted(calls) == sorted(segments)
