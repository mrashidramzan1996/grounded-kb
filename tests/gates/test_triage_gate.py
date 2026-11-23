from kb.gates.triage import triage_gate


def test_valid_has_no_untriaged(valid_ctx):
    assert triage_gate(valid_ctx) == []


def test_missing_triage_file_warns_for_every_segment(valid_ctx):
    valid_ctx.repo._triage.clear()
    issues = triage_gate(valid_ctx)
    assert len(issues) == len(valid_ctx.repo.segments("SRC-pricing-spec-v3"))
    assert {i.code for i in issues} == {"segment.untriaged"} and {i.severity for i in issues} == {"warning"}
    assert "`SRC-pricing-spec-v3#seg-01`" in issues[0].message
