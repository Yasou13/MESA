"""Offline-safe tokenizer loaders for deterministic dataset conversion."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

_O200K_BASE_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
_CL100K_BASE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"

_O200K_PATTERN = "|".join(
    [
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""\p{N}{1,3}""",
        r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
        r"""\s*[\r\n]+""",
        r"""\s+(?!\S)""",
        r"""\s+""",
    ]
)
_CL100K_PATTERN = r"""(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"""


@lru_cache(maxsize=None)
def _load_encoding(
    name: str,
    filename: str,
    expected_hash: str,
    pattern: str,
    special_tokens: tuple[tuple[str, int], ...],
) -> Any:
    import tiktoken
    from tiktoken.load import load_tiktoken_bpe

    asset = (
        resources.files("mesa_benchmark")
        .joinpath("resources")
        .joinpath("tokenizers")
        .joinpath(filename)
    )
    with resources.as_file(asset) as tokenizer_path:
        mergeable_ranks = load_tiktoken_bpe(
            str(tokenizer_path), expected_hash=expected_hash
        )
    return tiktoken.Encoding(
        name=name,
        pat_str=pattern,
        mergeable_ranks=mergeable_ranks,
        special_tokens=dict(special_tokens),
    )


def gpt4o_tokenizer() -> Any:
    """Return the packaged ``o200k_base`` encoding used by GPT-4o."""
    return _load_encoding(
        "o200k_base",
        "o200k_base.tiktoken",
        _O200K_BASE_SHA256,
        _O200K_PATTERN,
        (("<|endoftext|>", 199999), ("<|endofprompt|>", 200018)),
    )


def cl100k_tokenizer() -> Any:
    """Return the packaged ``cl100k_base`` encoding used by BEAM tools."""
    return _load_encoding(
        "cl100k_base",
        "cl100k_base.tiktoken",
        _CL100K_BASE_SHA256,
        _CL100K_PATTERN,
        (
            ("<|endoftext|>", 100257),
            ("<|fim_prefix|>", 100258),
            ("<|fim_middle|>", 100259),
            ("<|fim_suffix|>", 100260),
            ("<|endofprompt|>", 100276),
        ),
    )
