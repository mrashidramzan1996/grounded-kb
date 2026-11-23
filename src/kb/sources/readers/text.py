"""Markdown and plain text: already `#`-headed text; only whitespace is normalised."""

from kb.sources.readers import MAX_TEXT_CHARS, NormalisedText, SourceError


def read(data: bytes) -> NormalisedText:
    if len(data) > MAX_TEXT_CHARS * 4:  # a character is at most 4 bytes
        raise SourceError(f"text has more than {MAX_TEXT_CHARS} characters (MAX_TEXT_CHARS)")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise SourceError(f"text is not valid UTF-8: {e}") from e
    if len(raw) > MAX_TEXT_CHARS:
        raise SourceError(f"text has more than {MAX_TEXT_CHARS} characters (MAX_TEXT_CHARS)")
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    body = "\n".join(lines).rstrip("\n")
    return NormalisedText(body + "\n" if body else "", [(1, 0)])
