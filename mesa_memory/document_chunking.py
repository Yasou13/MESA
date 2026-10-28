"""Lossless document slices bounded by the bundled cl100k tokenizer."""

import re

from mesa_memory.adapter.tokenizer import count_tokens

_PARAGRAPH_END = re.compile(r"(?:\r?\n[ \t]*){2,}")
# Whitespace must follow inside the window: "12." at the window's edge may be
# the start of "12.03.2019", not a sentence end.
_SENTENCE_END = re.compile(r"[.!?]\s+")
_ABBREVIATIONS = frozenset({
    "md",
    "mad",
    "bkz",
    "vb",
    "vs",
    "sy",
    "sf",
    "no",
    "yarg",
    "hd",
    "cgk",
    "hgk",
    "av",
    "dr",
    "prof",
    "doç",
    "yrd",
    "ltd",
    "şti",
})
_TRAILING_WORD = re.compile(r"([^\W_]+)$")


def _is_sentence_end(prefix: str, m: re.Match) -> bool:
    if prefix[m.start()] in "!?":
        return True
    # Look only at the text just before the period; scanning the whole window
    # for every candidate doubled the time to chunk a 5 MB document.
    lookback = max(0, m.start() - 64)
    w_match = _TRAILING_WORD.search(prefix, lookback, m.start())
    if w_match and w_match.start() == lookback > 0:
        # The word may be longer than the look-back: read it whole.
        w_match = _TRAILING_WORD.search(prefix, 0, m.start())
    if not w_match:
        return True
    w = w_match.group(1)
    if w.isdigit():
        return False
    if len(w) == 1 and w.isalpha():
        return False
    norm = w.replace("İ", "i").replace("I", "ı").lower()
    if norm in _ABBREVIATIONS:
        return False
    return True


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
            para_boundaries = list(_PARAGRAPH_END.finditer(prefix))
            if para_boundaries:
                end = start + para_boundaries[-1].end()
            else:
                sentence_boundaries = list(_SENTENCE_END.finditer(prefix))
                if sentence_boundaries:
                    filtered = [
                        m for m in sentence_boundaries if _is_sentence_end(prefix, m)
                    ]
                    chosen = filtered or sentence_boundaries
                    end = start + chosen[-1].end()
        # Truncating at a natural boundary can change BPE merges at its end.
        while count_tokens(content[start:end], "openai") > 512:
            end -= 1
        chunks.append(content[start:end])
        start = end
    return chunks
