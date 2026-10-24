import logging
from pathlib import Path
from typing import Any, Optional

import tiktoken
from tiktoken.load import load_tiktoken_bpe

from mesa_memory.adapter.base import TokenBudgetExceededError
from mesa_memory.config import config

logger = logging.getLogger("MESA_Tokenizer")

_CL100K_HASH = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
_CL100K_PAT_STR = (
    r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"""
)
_CL100K_SPECIAL_TOKENS = {
    "<|endoftext|>": 100257,
    "<|fim_prefix|>": 100258,
    "<|fim_middle|>": 100259,
    "<|fim_suffix|>": 100260,
    "<|endofprompt|>": 100276,
}

_CL100K_ENCODING: tiktoken.Encoding | None = None
_TRANS_TOKENIZERS: dict[str, Any] = {}


def _offline_token_count(text: str) -> int:
    """Deterministic conservative count when the canonical BPE is unavailable.

    One token per UTF-8 byte deliberately overestimates common model
    tokenizers.  That keeps hard context bounds safe without a network or a
    pre-warmed tiktoken cache.
    """
    return len(text.encode("utf-8"))


def _get_cl100k_encoding() -> tiktoken.Encoding:
    global _CL100K_ENCODING
    if _CL100K_ENCODING is None:
        bundled_path = Path(__file__).parent / "data" / "cl100k_base.tiktoken"
        mergeable_ranks = load_tiktoken_bpe(str(bundled_path), expected_hash=_CL100K_HASH)
        _CL100K_ENCODING = tiktoken.Encoding(
            name="cl100k_base",
            pat_str=_CL100K_PAT_STR,
            mergeable_ranks=mergeable_ranks,
            special_tokens=_CL100K_SPECIAL_TOKENS,
        )
    return _CL100K_ENCODING


def count_tokens(
    text: str,
    adapter_type: str,
    model_id: str = "",
    *,
    strict: bool = False,
) -> int:
    if adapter_type in ("claude", "openai"):
        try:
            enc = _get_cl100k_encoding()
            return len(enc.encode(text))
        except Exception as exc:
            logger.warning(
                "tiktoken encoding is unavailable, using conservative offline count: %s",
                exc,
            )
            return _offline_token_count(text)
    if adapter_type == "ollama":
        try:
            if model_id not in _TRANS_TOKENIZERS:
                from transformers import AutoTokenizer

                _TRANS_TOKENIZERS[model_id] = AutoTokenizer.from_pretrained(model_id)
            tokenizer = _TRANS_TOKENIZERS[model_id]
            return len(tokenizer.encode(text))
        except (OSError, ValueError, ImportError) as exc:
            if strict:
                raise RuntimeError("configured tokenizer is unavailable") from exc
            logger.warning(
                "AutoTokenizer.from_pretrained(%s) failed, using word-count estimate: %s",
                model_id,
                exc,
            )
            return int(len(text.split()) * 1.3)
    raise ValueError(f"Unknown adapter_type: {adapter_type}")


def enforce_context_limit(  # type: ignore[no-untyped-def]
    text: str, adapter_type: str, model_id: str, limit: Optional[int] = None
):
    effective_limit = limit if limit is not None else config.context_window_limit
    token_count = count_tokens(text, adapter_type, model_id)
    if token_count > effective_limit:
        raise TokenBudgetExceededError(
            f"Token count {token_count} exceeds limit {effective_limit}"
        )
