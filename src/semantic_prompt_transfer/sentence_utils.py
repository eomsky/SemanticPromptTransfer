from __future__ import annotations

from collections.abc import Iterator


def sentence_spans(text: str) -> Iterator[tuple[int, int]]:
    """Yield sentence spans without treating decimal/date dots as boundaries.

    Newlines are hard boundaries. A dot between two digits is protected so values such
    as 58.3%, 134.0%, 2026.03.23 and 3.14 remain inside one claim.
    """
    value = str(text or "")
    start = 0
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\n":
            if start < index and value[start:index].strip():
                yield start, index
            index += 1
            start = index
            continue
        if char in ".!?":
            if (
                char == "."
                and index > 0
                and index + 1 < length
                and value[index - 1].isdigit()
                and value[index + 1].isdigit()
            ):
                index += 1
                continue
            end = index + 1
            while end < length and value[end] in ".!?":
                end += 1
            if value[start:end].strip():
                yield start, end
            start = end
            index = end
            continue
        index += 1
    if start < length and value[start:].strip():
        yield start, length
