import asyncio
import functools
import logging
from typing import Optional, Type, Union

import anthropic
from pydantic import BaseModel

from mesa_memory.adapter.base import BaseUniversalLLMAdapter, TokenBudgetExceededError
from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.config import config

logger = logging.getLogger("MESA_Adapter")

# ---------------------------------------------------------------------------
# Optional imports — deferred to avoid hard crashes when either SDK is absent
# ---------------------------------------------------------------------------
try:
    import openai as _openai_module
except ImportError:
    _openai_module = None

try:
    import torch
    from transformers import AutoTokenizer, AutoModel
    _LOCAL_EMBED_AVAILABLE = True
except ImportError:
    _LOCAL_EMBED_AVAILABLE = False


# ---------------------------------------------------------------------------
# Local embedding singleton (all-MiniLM-L6-v2, ~22 MB on disk)
# ---------------------------------------------------------------------------
_local_embed_model = None
_local_embed_tokenizer = None


def _get_local_embed_components():
    """Lazily load the local embedding model once per process."""
    global _local_embed_model, _local_embed_tokenizer
    if _local_embed_model is None:
        if not _LOCAL_EMBED_AVAILABLE:
            raise ImportError(
                "Neither openai nor torch+transformers is available. "
                "Install one to enable embeddings for ClaudeAdapter."
            )
        model_name = config.local_embedding_model
        logger.info(
            "Loading local embedding model '%s' (OpenAI API key not provided)",
            model_name,
        )
        _local_embed_tokenizer = AutoTokenizer.from_pretrained(model_name)
        _local_embed_model = AutoModel.from_pretrained(model_name)
        _local_embed_model.eval()
    return _local_embed_tokenizer, _local_embed_model


def _local_embed(text: str) -> list[float]:
    """Generate an embedding using the local transformer model.

    Uses mean pooling over token embeddings with attention-mask weighting,
    matching the canonical sentence-transformers approach.
    """
    tokenizer, model = _get_local_embed_components()
    encoded = tokenizer(
        text, padding=True, truncation=True, max_length=512, return_tensors="pt"
    )
    with torch.no_grad():
        outputs = model(**encoded)
    # Mean pooling
    attention_mask = encoded["attention_mask"]
    token_embeddings = outputs.last_hidden_state
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    summed = torch.sum(token_embeddings * input_mask_expanded, dim=1)
    counted = torch.clamp(input_mask_expanded.sum(dim=1), min=1e-9)
    embedding = (summed / counted).squeeze().tolist()
    # Ensure list[float] even for single-dim edge cases
    if isinstance(embedding, float):
        embedding = [embedding]
    return embedding


class ClaudeAdapter(BaseUniversalLLMAdapter):
    def __init__(self, anthropic_api_key: Optional[str] = None, openai_api_key: Optional[str] = None):
        self.openai_api_key = openai_api_key
        self._sync_anthropic = anthropic.Anthropic(api_key=anthropic_api_key)
        self._async_anthropic = anthropic.AsyncAnthropic(api_key=anthropic_api_key)

        if openai_api_key and _openai_module is not None:
            self._sync_openai = _openai_module.OpenAI(api_key=openai_api_key)
            self._async_openai = _openai_module.AsyncOpenAI(api_key=openai_api_key)
        else:
            self._sync_openai = None
            self._async_openai = None

    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)
        model = kwargs.get("model", "claude-sonnet-4-20250514")

        response = self._sync_anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text

        if schema is not None:
            return schema.model_validate_json(text)
        return text

    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)
        model = kwargs.get("model", "claude-sonnet-4-20250514")

        response = await self._async_anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text

        if schema is not None:
            return schema.model_validate_json(text)
        return text

    def embed(self, text: str, **kwargs) -> list[float]:
        if self._sync_openai:
            model = kwargs.get("model", "text-embedding-3-small")
            response = self._sync_openai.embeddings.create(
                model=model,
                input=text,
            )
            return response.data[0].embedding

        # Graceful fallback: local transformer embedding
        logger.debug("Using local embedding fallback (no OpenAI key)")
        return _local_embed(text)

    async def aembed(self, text: str, **kwargs) -> list[float]:
        if self._async_openai:
            model = kwargs.get("model", "text-embedding-3-small")
            response = await self._async_openai.embeddings.create(
                model=model,
                input=text,
            )
            return response.data[0].embedding

        # Graceful fallback: run local embedding in executor to avoid blocking
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(_local_embed, text),
        )

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="claude")
