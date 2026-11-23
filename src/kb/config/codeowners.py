from pathspec import GitIgnoreSpec


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        rules.append((pattern, owners))
    return rules


def owners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    result: list[str] = []
    for pattern, owners in rules:
        if GitIgnoreSpec.from_lines([pattern]).match_file(path):
            result = owners
    return result
