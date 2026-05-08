from typing import Optional

import tiktoken
from transformers import AutoTokenizer

from mesa_memory.config import config
from mesa_memory.adapter.base import TokenBudgetExceededError


def count_tokens(text: str, adapter_type: str, model_id: str = "") -> int:
    if adapter_type == "claude":
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    if adapter_type == "ollama":
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_id)
            return len(tokenizer.encode(text))
        except Exception:
            return int(len(text.split()) * 1.3)
    raise ValueError(f"Unknown adapter_type: {adapter_type}")


def enforce_context_limit(text: str, adapter_type: str, model_id: str, limit: Optional[int] = None):
    effective_limit = limit if limit is not None else config.context_window_limit
    token_count = count_tokens(text, adapter_type, model_id)
    if token_count > effective_limit:
        raise TokenBudgetExceededError(
            f"Token count {token_count} exceeds limit {effective_limit}"
        )
