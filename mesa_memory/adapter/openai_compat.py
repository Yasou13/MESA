"""Single OpenAI-compatible chat adapter for MESA (D102)."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any, Optional, Type, Union

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]
from pydantic import BaseModel

from mesa_memory.adapter.base import (
    BaseUniversalLLMAdapter,
    ChatCompletionResult,
    LLMResponseError,
)
from mesa_memory.adapter.endpoints import is_local_endpoint
from mesa_memory.adapter.tokenizer import count_tokens

logger = logging.getLogger("MESA_Adapter")


def _bad_request_errors() -> tuple[type[BaseException], ...]:
    if openai is None:
        return ()
    err = getattr(openai, "BadRequestError", None)
    return (err,) if isinstance(err, type) else ()


def _not_found_errors() -> tuple[type[BaseException], ...]:
    if openai is None:
        return ()
    err = getattr(openai, "NotFoundError", None)
    return (err,) if isinstance(err, type) else ()


def _json_decode_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [json.JSONDecodeError]
    if openai is not None:
        api_err = getattr(openai, "APIError", None)
        if isinstance(api_err, type):
            errors.append(api_err)
    return tuple(errors)


class OpenAICompatibleAdapter(BaseUniversalLLMAdapter):
    """Single OpenAI-compatible adapter for OpenAI, Ollama (/v1), vLLM, LM Studio, OpenRouter, etc."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: float = 60.0,
        embedding_model_name: Optional[str] = None,
    ):
        if openai is None:
            raise RuntimeError("OpenAICompatibleAdapter requires the 'openai' package.")

        self.base_url = base_url.strip() if isinstance(base_url, str) and base_url.strip() else None
        self.model_name = (model_name or "").strip()
        self.embedding_model_name = embedding_model_name or "text-embedding-3-small"
        self.timeout_seconds = float(timeout_seconds)

        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite value")

        effective_key = (api_key or "").strip() if isinstance(api_key, str) else ""
        if not effective_key:
            if self.base_url and is_local_endpoint(self.base_url):
                effective_key = "local"
            else:
                raise ValueError(
                    "API key is required for OpenAICompatibleAdapter. "
                    "Set MESA_LLM_API_KEY environment variable."
                )
        self.api_key = effective_key

        client_options: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout_seconds,
            "max_retries": 0,
        }
        self._sync_client = openai.OpenAI(**client_options)
        self._async_client = openai.AsyncOpenAI(**client_options)

    @staticmethod
    def _sanitize_json(text: str) -> str:
        """Extract clean JSON from LLM output that may contain markdown fences or prose."""
        text = text.strip()
        match = re.search(
            r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
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

    @staticmethod
    def _extract_result(
        response: Any,
        *,
        fallback_model: str,
        latency_ms: float,
    ) -> ChatCompletionResult:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, (list, tuple)) or not choices:
            raise LLMResponseError("LLM returned a malformed response with no choices")
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            raise LLMResponseError("LLM returned a malformed choice with no message")
        content = getattr(message, "content", None)
        if content is None or not isinstance(content, str):
            raise LLMResponseError("LLM returned a malformed message with non-text content")

        usage = getattr(response, "usage", None)
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(
                getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            )

        model = str(getattr(response, "model", None) or fallback_model or "")
        return ChatCompletionResult(
            text=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    @classmethod
    def _parse_schema(cls, text: str, schema: Type[BaseModel]) -> BaseModel:
        sanitized = cls._sanitize_json(text)
        try:
            parsed_data = json.loads(sanitized)
            if isinstance(parsed_data, list) and "results" in getattr(
                schema, "model_fields", {}
            ):
                parsed_data = {"results": parsed_data}
            return schema.model_validate(parsed_data)
        except json.JSONDecodeError:
            return schema.model_validate_json(sanitized)

    @staticmethod
    def _schema_response_format(schema: Type[BaseModel]) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
            },
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        target_model = model if model is not None else self.model_name
        request_kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        started = time.perf_counter()
        try:
            response = self._sync_client.chat.completions.create(**request_kwargs)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM returned invalid JSON body: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._extract_result(
            response, fallback_model=target_model, latency_ms=latency_ms
        )

    async def achat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        target_model = model if model is not None else self.model_name
        request_kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        started = time.perf_counter()
        try:
            response = await self._async_client.chat.completions.create(**request_kwargs)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM returned invalid JSON body: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        return self._extract_result(
            response, fallback_model=target_model, latency_ms=latency_ms
        )

    def _build_messages(self, prompt: str, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})
        messages.append({"role": "user", "content": prompt})
        return messages

    def complete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs: Any
    ) -> Union[str, BaseModel]:
        max_tokens = int(kwargs.get("max_tokens", 1024))
        default_temp = 0.0 if schema is not None else 0.7
        temperature = kwargs.get("temperature", default_temp)
        model = kwargs.get("model", self.model_name)
        messages = self._build_messages(prompt, kwargs)

        response_format = kwargs.get("response_format")
        if schema is not None and response_format is None:
            response_format = self._schema_response_format(schema)

        try:
            result = self.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
                response_format=response_format,
            )
        except _bad_request_errors():
            if schema is None or response_format is None:
                raise
            # Some OpenAI-compatible servers reject response_format=json_schema; retry plain.
            result = self.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
                response_format=None,
            )

        if schema is not None:
            return self._parse_schema(result.text, schema)
        return result.text

    async def acomplete(
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs: Any
    ) -> Union[str, BaseModel]:
        max_tokens = int(kwargs.get("max_tokens", 1024))
        default_temp = 0.0 if schema is not None else 0.7
        temperature = kwargs.get("temperature", default_temp)
        model = kwargs.get("model", self.model_name)
        messages = self._build_messages(prompt, kwargs)

        response_format = kwargs.get("response_format")
        if schema is not None and response_format is None:
            response_format = self._schema_response_format(schema)

        try:
            result = await self.achat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
                response_format=response_format,
            )
        except _bad_request_errors():
            if schema is None or response_format is None:
                raise
            result = await self.achat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
                response_format=None,
            )

        if schema is not None:
            return self._parse_schema(result.text, schema)
        return result.text

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        model = kwargs.get("model", self.embedding_model_name)
        try:
            response = self._sync_client.embeddings.create(model=model, input=text)
            return list(response.data[0].embedding)
        except _not_found_errors() as exc:
            raise RuntimeError(
                "configured external embedding model is unavailable"
            ) from exc

    async def aembed(self, text: str, **kwargs: Any) -> list[float]:
        model = kwargs.get("model", self.embedding_model_name)
        try:
            response = await self._async_client.embeddings.create(
                model=model, input=text
            )
            return list(response.data[0].embedding)
        except _not_found_errors() as exc:
            raise RuntimeError(
                "configured external embedding model is unavailable"
            ) from exc

    def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        model = kwargs.get("model", self.embedding_model_name)
        try:
            response = self._sync_client.embeddings.create(model=model, input=texts)
            return [
                list(data.embedding)
                for data in sorted(response.data, key=lambda item: item.index)
            ]
        except _not_found_errors() as exc:
            raise RuntimeError(
                "configured external embedding model is unavailable"
            ) from exc

    async def aembed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        model = kwargs.get("model", self.embedding_model_name)
        try:
            response = await self._async_client.embeddings.create(
                model=model, input=texts
            )
            return [
                list(data.embedding)
                for data in sorted(response.data, key=lambda item: item.index)
            ]
        except _not_found_errors() as exc:
            raise RuntimeError(
                "configured external embedding model is unavailable"
            ) from exc

    def get_token_count(self, text: str) -> int:
        return count_tokens(text, adapter_type="openai")
