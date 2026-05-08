from typing import Optional, Type, Union

import anthropic
import openai
from pydantic import BaseModel

from mesa_memory.adapter.base import BaseUniversalLLMAdapter, TokenBudgetExceededError
from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.config import config


class ClaudeAdapter(BaseUniversalLLMAdapter):
    EMBEDDING_DIM = 1536

    def __init__(self, anthropic_api_key: Optional[str] = None, openai_api_key: Optional[str] = None):
        self._sync_anthropic = anthropic.Anthropic(api_key=anthropic_api_key)
        self._async_anthropic = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
        self._sync_openai = openai.OpenAI(api_key=openai_api_key)
        self._async_openai = openai.AsyncOpenAI(api_key=openai_api_key)

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
        model = kwargs.get("model", "text-embedding-3-small")
        response = self._sync_openai.embeddings.create(
            model=model,
            input=text,
        )
        return response.data[0].embedding

    async def aembed(self, text: str, **kwargs) -> list[float]:
        model = kwargs.get("model", "text-embedding-3-small")
        response = await self._async_openai.embeddings.create(
            model=model,
            input=text,
        )
        return response.data[0].embedding

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="claude")
