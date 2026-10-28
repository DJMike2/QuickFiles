"""Short model outputs reference exact passages from that same file."""
from app.ai.evidence import compact


def choices(source):
    source = compact(source)
    passages, start = [''], 0
    while start < len(source):
        end = min(len(source), start + 140)
        if end < len(source):
            boundary = source.rfind(' ', start + 70, end)
            if boundary > start:
                end = boundary
        passage = source[start:end].strip()
        if passage:
            passages.append(passage)
        start = end
        while start < len(source) and source[start].isspace():
            start += 1
    return passages


def numbered(passages):
    return {str(i): passage for i, passage in enumerate(passages) if i}


def restore(value, passages):
    if type(value) is int:
        return passages[value] if 0 <= value < len(passages) else ''
    # Older local adapters can still supply a literal string. The caller applies
    # the same strict source grounding checks as before; no text is trusted here.
    if isinstance(value, str):
        return value
    raise ValueError('Invalid evidence quotation index')
