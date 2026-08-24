import logging
from typing import Any, Optional

import tiktoken

from mesa_memory.adapter.base import TokenBudgetExceededError
from mesa_memory.config import config

logger = logging.getLogger("MESA_Tokenizer")


_CL100K_ENCODING: tiktoken.Encoding | None = None
_TRANS_TOKENIZERS: dict[str, Any] = {}


def _get_cl100k_encoding() -> tiktoken.Encoding:
    global _CL100K_ENCODING
    if _CL100K_ENCODING is None:
        _CL100K_ENCODING = tiktoken.get_encoding("cl100k_base")
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
            if strict:
                raise RuntimeError("canonical tokenizer is unavailable") from exc
            # tiktoken lazily downloads its encoding table on a cold cache.
            # Token budgeting must remain available in offline CI and local
            # development, so use the same conservative fallback as Ollama.
            logger.warning(
                "tiktoken encoding is unavailable, using word-count estimate: %s", exc
            )
            return int(len(text.split()) * 1.3)
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
