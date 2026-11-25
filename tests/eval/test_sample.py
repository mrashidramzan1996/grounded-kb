"""`kb eval sample`: a deterministic QC checklist for a pull-request description."""

import json
import re

from helpers import git, invoke
from judge_fixture import ingested_with_run_b, valid

from kb.eval.judge import JUDGE_ITEMS, VERDICTS
from kb.yamlio import dump_yaml


def test_sample_is_deterministic_and_has_no_verdicts(tmp_path):
    root = ingested_with_run_b(tmp_path)
    assert invoke(root, "eval", "judge").exit_code == 0
    items = json.loads((root / JUDGE_ITEMS).read_text())
    verdicts = valid(items)
    verdicts["R:REQ-F-001"] |= {"same_actor": "no", "note": "a distinctive judge note"}
    (root / VERDICTS).write_text(dump_yaml({"verdicts": list(verdicts.values())}))
    assert invoke(root, "eval", "report", "--write").exit_code == 0
    first = invoke(root, "eval", "sample", "--n", "2")
    assert first.exit_code == 0, first.output
    out = first.stdout
    assert invoke(root, "eval", "sample", "--n", "2").stdout == out  # the same HEAD, the same sample
    head = git(root, "rev-parse", "HEAD").strip()
    assert head[:12] in out
    boxes = [line for line in out.splitlines() if line.startswith("- [ ] ")]
    records = [b for b in boxes if re.match(r"- \[ \] `(REQ|EXCL|ADR|INV|TERM)-", b)]
    assert len(records) == 2 and len(boxes) == 4  # 2 records, 2 sentences: one rule-like, one not
    assert "rule-like" in out and "not rule-like" in out
    # each record with its quote; no verdict, note or finding of the judge
    assert all("quote" in b for b in records)
    for word in ("verdict", "same_actor", "distinctive judge note", "missing", "unfaithful"):
        assert word not in out
    assert not re.search(r"\bF-[0-9a-f]{10}\b", out)
    # the default is 10 of each, as many as there are; --n must be positive
    assert invoke(root, "eval", "sample").exit_code == 0
    assert invoke(root, "eval", "sample", "--n", "0").exit_code == 2
