from kb.config.codeowners import owners_for, parse_codeowners


def test_codeowners_last_match_wins():
    rules = parse_codeowners("* @acme/all\n/services/billing/ @acme/billing\n# c\n")
    assert owners_for("services/billing/x.py", rules) == ["@acme/billing"] and owners_for("x", rules) == ["@acme/all"]
