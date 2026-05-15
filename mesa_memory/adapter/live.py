import re
import json
import logging
from typing import Optional, Type, Union

import openai
from pydantic import BaseModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.adapter.tokenizer import count_tokens

logger = logging.getLogger("MESA_Adapter")


class OpenAICompatibleAdapter(BaseUniversalLLMAdapter):
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name or "llama-3.1-8b-instant"

        if not api_key or not api_key.startswith("gsk_"):
            raise ValueError(
                f"CRITICAL AUTH FAILURE: Valid Groq API key not loaded. Received: {str(api_key)[:10]}..."
            )

        self._sync_client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        self._async_client = openai.AsyncOpenAI(
            api_key=self.api_key, base_url=self.base_url
        )

    @staticmethod
    def _sanitize_json(text: str) -> str:
        """Extract clean JSON from LLM output that may contain markdown fences or prose."""
        text = text.strip()
        # Strategy 1: Extract from markdown code fences
        match = re.search(
            r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        # Strategy 2: Find the outermost JSON structure (object or array)
        start_idx = text.find("{")
        arr_start_idx = text.find("[")
        if start_idx == -1 or (arr_start_idx != -1 and arr_start_idx < start_idx):
            start_idx = arr_start_idx
        end_idx = text.rfind("}")
        arr_end_idx = text.rfind("]")
        if end_idx == -1 or (arr_end_idx != -1 and arr_end_idx > end_idx):
            end_idx = arr_end_idx
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            return text[start_idx : end_idx + 1]
        return text

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    def complete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]:
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)
        model = kwargs.get("model", self.model_name)

        try:
            response = self._sync_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = response.choices[0].message.content or ""

            if schema is not None:
                text = self._sanitize_json(text)
                try:
                    parsed_data = json.loads(text)
                    if isinstance(parsed_data, list):
                        parsed_data = {"results": parsed_data}
                    return schema.model_validate(parsed_data)
                except json.JSONDecodeError:
                    return schema.model_validate_json(text)
            return text
        except openai.RateLimitError as e:
            logger.error("Rate limit error: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    async def acomplete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]:
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 0.7)
        model = kwargs.get("model", self.model_name)

        try:
            response = await self._async_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = response.choices[0].message.content or ""

            if schema is not None:
                text = self._sanitize_json(text)
                try:
                    parsed_data = json.loads(text)
                    if isinstance(parsed_data, list):
                        parsed_data = {"results": parsed_data}
                    return schema.model_validate(parsed_data)
                except json.JSONDecodeError:
                    return schema.model_validate_json(text)
            return text
        except openai.RateLimitError as e:
            logger.error("Rate limit error: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    def embed(self, text: str, **kwargs) -> list[float]:
        model = kwargs.get("model", "text-embedding-3-small")
        try:
            response = self._sync_client.embeddings.create(
                model=model,
                input=text,
            )
            return response.data[0].embedding
        except openai.NotFoundError:
            logger.debug("Using local embedding fallback for Groq")
            from mesa_memory.adapter.claude import _local_embed

            return _local_embed(text)
        except openai.RateLimitError as e:
            logger.error("Rate limit error during embedding: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error during embedding: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    async def aembed(self, text: str, **kwargs) -> list[float]:
        model = kwargs.get("model", "text-embedding-3-small")
        try:
            response = await self._async_client.embeddings.create(
                model=model,
                input=text,
            )
            return response.data[0].embedding
        except openai.NotFoundError:
            import asyncio
            import functools
            from mesa_memory.adapter.claude import _local_embed

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, functools.partial(_local_embed, text)
            )
        except openai.RateLimitError as e:
            logger.error("Rate limit error during async embedding: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error during async embedding: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        model = kwargs.get("model", "text-embedding-3-small")
        try:
            response = self._sync_client.embeddings.create(
                model=model,
                input=texts,
            )
            return [
                data.embedding for data in sorted(response.data, key=lambda x: x.index)
            ]
        except openai.NotFoundError:
            from mesa_memory.adapter.claude import _local_embed_batch

            return _local_embed_batch(texts)
        except openai.RateLimitError as e:
            logger.error("Rate limit error during batch embedding: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error during batch embedding: %s", e)
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError)
        ),
    )
    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        model = kwargs.get("model", "text-embedding-3-small")
        try:
            response = await self._async_client.embeddings.create(
                model=model,
                input=texts,
            )
            return [
                data.embedding for data in sorted(response.data, key=lambda x: x.index)
            ]
        except openai.NotFoundError:
            import asyncio
            import functools
            from mesa_memory.adapter.claude import _local_embed_batch

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, functools.partial(_local_embed_batch, texts)
            )
        except openai.RateLimitError as e:
            logger.error("Rate limit error during async batch embedding: %s", e)
            raise
        except openai.APIConnectionError as e:
            logger.error("API connection error during async batch embedding: %s", e)
            raise

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="openai")
