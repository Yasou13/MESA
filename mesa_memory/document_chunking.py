"""Lossless document slices bounded by the bundled cl100k tokenizer."""

import re

from mesa_memory.adapter.tokenizer import count_tokens

_PARAGRAPH_END = re.compile(r"(?:\r?\n[ \t]*){2,}")
# Whitespace must follow inside the window: "12." at the window's edge may be
# the start of "12.03.2019", not a sentence end.
_SENTENCE_END = re.compile(r"[.!?]\s+")


def chunk_document(content: str) -> list[str]:
    """Prefer blank lines, then sentence ends, then Unicode-safe hard cuts.

    Keep every character, including separator whitespace, in exactly one slice.
    The character ceiling also respects the existing single-chunk admission API.
    """
    chunks = []
    start = 0
    while start < len(content):
        ceiling = min(len(content), start + 32768)
        low, high = start, min(start + 512, ceiling)
        while count_tokens(content[start:high], "openai") <= 512:
            low = high
            if high == ceiling:
                break
            high = min(start + (high - start) * 2, ceiling)
        while low + 1 < high:
            middle = (low + high) // 2
            if count_tokens(content[start:middle], "openai") <= 512:
                low = middle
            else:
                high = middle
        end = low
        if end < len(content):
            prefix = content[start:end]
            boundaries = list(_PARAGRAPH_END.finditer(prefix)) or list(
                _SENTENCE_END.finditer(prefix)
            )
            if boundaries:
                end = start + boundaries[-1].end()
        # Truncating at a natural boundary can change BPE merges at its end.
        while count_tokens(content[start:end], "openai") > 512:
            end -= 1
        chunks.append(content[start:end])
        start = end
    return chunks
