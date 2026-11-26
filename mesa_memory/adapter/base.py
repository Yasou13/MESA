from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Type, Union

from pydantic import BaseModel

from mesa_memory.config import config


class TokenBudgetExceededError(Exception):
    pass


class LLMNotConfiguredError(ValueError):
    """Raised when an LLM feature is invoked with no LLM configured (D101)."""


class LLMResponseError(ValueError):
    """Raised when an OpenAI-compatible server returns a malformed response."""


@dataclass(frozen=True)
class ChatCompletionResult:
    """Structured result from one chat completion call."""

    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class BaseUniversalLLMAdapter(ABC):
    @property
    def EMBEDDING_DIM(self) -> int:
        return config.embedding_dimension

    @abstractmethod
    def complete(  # type: ignore[no-untyped-def]
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]: ...

    @abstractmethod
    async def acomplete(  # type: ignore[no-untyped-def]
        self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs
    ) -> Union[str, BaseModel]: ...

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        prompt = "\n\n".join(
            str(message.get("content", "")) for message in messages if message.get("content")
        )
        kwargs: dict[str, Any] = {"max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if model is not None:
            kwargs["model"] = model
        if response_format is not None:
            kwargs["response_format"] = response_format
        text = str(self.complete(prompt, **kwargs))
        return ChatCompletionResult(text=text, model=model or "")

    async def achat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        prompt = "\n\n".join(
            str(message.get("content", "")) for message in messages if message.get("content")
        )
        kwargs: dict[str, Any] = {"max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if model is not None:
            kwargs["model"] = model
        if response_format is not None:
            kwargs["response_format"] = response_format
        text = str(await self.acomplete(prompt, **kwargs))
        return ChatCompletionResult(text=text, model=model or "")

    @abstractmethod
    def embed(
        self, text: str, **kwargs
    ) -> list[float]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    async def aembed(
        self, text: str, **kwargs
    ) -> list[float]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def embed_batch(
        self, texts: list[str], **kwargs
    ) -> list[list[float]]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    async def aembed_batch(
        self, texts: list[str], **kwargs
    ) -> list[list[float]]: ...  # type: ignore[no-untyped-def]

    @abstractmethod
    def get_token_count(self, text: str) -> int: ...
