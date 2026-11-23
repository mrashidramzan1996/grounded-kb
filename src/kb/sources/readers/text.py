"""Markdown and plain text: already `#`-headed text; only whitespace is normalised."""

from kb.sources.readers import NormalisedText, SourceError


def read(data: bytes) -> NormalisedText:
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise SourceError(f"text is not valid UTF-8: {e}") from e
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    body = "\n".join(lines).rstrip("\n")
    return NormalisedText(body + "\n" if body else "", [(1, 0)])
