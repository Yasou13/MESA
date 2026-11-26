"""
MESA Adapter Factory — single OpenAI-compatible chat adapter (D102).

Configure chat models with:
  - MESA_LLM_MODEL    (e.g. "gpt-4o-mini", "qwen3:8b", "mock")
  - MESA_LLM_BASE_URL (e.g. "http://localhost:11434/v1", optional for OpenAI)
  - MESA_LLM_API_KEY  (optional for local endpoints)

Legacy 'provider:model' specs (e.g. 'ollama:qwen3:8b', 'openai:gpt-4o-mini')
are accepted with a one-time deprecation warning.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from mesa_memory.adapter.base import (
    BaseUniversalLLMAdapter,
    ChatCompletionResult,
    LLMNotConfiguredError,
)
from mesa_memory.adapter.endpoints import is_local_endpoint
from mesa_memory.config import config

logger = logging.getLogger("MESA_AdapterFactory")

SUPPORTED_LLM_PROVIDERS: tuple[str, ...] = (
    "openai_compatible",
    "mock",
)

_LEGACY_LLM_PREFIXES: frozenset[str] = frozenset(
    {"ollama", "openai", "openai_compatible", "anthropic", "claude"}
)
_EXPLICIT_EXTERNAL_ONLY_PREFIXES: frozenset[str] = frozenset(
    {"openai", "claude", "anthropic", "hosted"}
)
_WARNED_LEGACY_SPECS: set[str] = set()


def reset_legacy_warnings() -> None:
    """Clear tracked one-time legacy spec warnings (test helper)."""
    _WARNED_LEGACY_SPECS.clear()


def _normalize_ollama_v1_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def _warn_legacy_spec_once(spec: str, model: str, resolved_base_url: str | None) -> None:
    key = spec.strip()
    if key in _WARNED_LEGACY_SPECS:
        return
    _WARNED_LEGACY_SPECS.add(key)
    if resolved_base_url:
        logger.warning(
            "MESA_LLM_MODEL='%s' uses deprecated 'provider:model' syntax; "
            "set MESA_LLM_MODEL='%s' and MESA_LLM_BASE_URL='%s' instead.",
            key,
            model,
            resolved_base_url,
        )
    else:
        logger.warning(
            "MESA_LLM_MODEL='%s' uses deprecated 'provider:model' syntax; "
            "set MESA_LLM_MODEL='%s' (and MESA_LLM_BASE_URL if needed) instead.",
            key,
            model,
        )


def parse_llm_spec(spec: str) -> tuple[str, str]:
    """Parse MESA_LLM_MODEL into (provider_hint, model_name).

    Plain model names (including Ollama tags like 'qwen3:8b') resolve to
    ('openai_compatible', model_name). Legacy 'provider:model' prefixes
    ('ollama:', 'openai:', 'openai_compatible:', 'anthropic:', 'claude:')
    are parsed so callers can strip them with a one-time warning.
    """
    stripped = (spec or "").strip()
    if not stripped:
        raise LLMNotConfiguredError(
            "no LLM configured. Set MESA_LLM_MODEL (and MESA_LLM_BASE_URL for local or custom endpoints)."
        )

    if stripped.lower() == "mock":
        return "mock", "mock"

    if ":" in stripped:
        prefix, rest = stripped.split(":", 1)
        normalized_prefix = prefix.strip().lower()
        model = rest.strip()
        if normalized_prefix == "mock":
            return "mock", model or "mock"
        if normalized_prefix in _LEGACY_LLM_PREFIXES:
            if not model:
                raise ValueError(
                    f"Empty model in LLM spec '{spec}'. Set MESA_LLM_MODEL to a model name."
                )
            return normalized_prefix, model

    return "openai_compatible", stripped


class DeterministicMockAdapter(BaseUniversalLLMAdapter):
    """Zero-dependency mock adapter for testing and CI environments.

    Returns deterministic, non-random outputs.  Should NEVER be used in
    production — emits a CRITICAL warning on construction.
    """

    def __init__(self):  # type: ignore[no-untyped-def]
        logger.critical(
            "DeterministicMockAdapter active — no real LLM provider detected. "
            "Set MESA_LLM_MODEL and MESA_LLM_BASE_URL (or MESA_LLM_API_KEY) to "
            "enable a real provider."
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        text = "[MOCK] Deterministic response"
        prompt_tokens = sum(
            self.get_token_count(str(m.get("content", ""))) for m in messages
        )
        completion_tokens = self.get_token_count(text)
        return ChatCompletionResult(
            text=text,
            model=model or "mock",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=0.0,
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
        return self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            response_format=response_format,
        )

    def complete(self, prompt, schema=None, **kwargs):  # type: ignore[no-untyped-def]
        text = "[MOCK] Deterministic response"
        if schema is not None:
            list_fields = [
                name
                for name, field in schema.model_fields.items()
                if getattr(field.annotation, "__origin__", None) is list
            ]
            if len(list_fields) != 1:
                raise ValueError(
                    "deterministic mock requires one list-root schema field"
                )
            return schema.model_validate({list_fields[0]: []})
        return text

    async def acomplete(self, prompt, schema=None, **kwargs):  # type: ignore[no-untyped-def]
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text, **kwargs):  # type: ignore[no-untyped-def]
        import hashlib

        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [(b / 255.0) - 0.5 for b in h]
        if len(vec) < config.embedding_dimension:
            vec = vec * (config.embedding_dimension // len(vec) + 1)
        vec = vec[: config.embedding_dimension]
        mag = sum(x**2 for x in vec) ** 0.5
        if mag == 0:
            return [1.0] + [0.0] * (config.embedding_dimension - 1)
        return [x / mag for x in vec]

    async def aembed(self, text, **kwargs):  # type: ignore[no-untyped-def]
        return self.embed(text, **kwargs)

    def embed_batch(self, texts, **kwargs):  # type: ignore[no-untyped-def]
        return [self.embed(t) for t in texts]

    async def aembed_batch(self, texts, **kwargs):  # type: ignore[no-untyped-def]
        return self.embed_batch(texts, **kwargs)

    def get_token_count(self, text):  # type: ignore[no-untyped-def]
        return len(text.split())


class AdapterFactory:
    @staticmethod
    def is_llm_configured() -> bool:
        """Return whether MESA_LLM_MODEL is configured."""
        return bool((config.llm_model or "").strip())

    @staticmethod
    def get_adapter(
        provider: Optional[str] = None,
        *,
        model_name: str | None = None,
        thinking: bool | None = None,
    ) -> BaseUniversalLLMAdapter:
        del thinking  # Kept for signature compatibility with callers.
        raw_spec: str | None = None
        legacy_prefix: str | None = None

        if provider is None:
            raw_spec = (config.llm_model or "").strip()
            if not raw_spec:
                raise LLMNotConfiguredError(
                    "no LLM configured. Set MESA_LLM_MODEL (and MESA_LLM_BASE_URL for local or custom endpoints)."
                )
            parsed_provider, parsed_model = parse_llm_spec(raw_spec)
            if parsed_provider == "mock":
                return DeterministicMockAdapter()
            if ":" in raw_spec and raw_spec.split(":", 1)[0].strip().lower() in _LEGACY_LLM_PREFIXES:
                legacy_prefix = parsed_provider
            resolved_model = model_name if model_name is not None else parsed_model
        else:
            normalized_provider = provider.strip().lower()
            if normalized_provider == "mock":
                return DeterministicMockAdapter()
            if normalized_provider in _EXPLICIT_EXTERNAL_ONLY_PREFIXES and not getattr(
                config, "external_provider_enabled", False
            ):
                raise ValueError(
                    f"External provider '{provider}' is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
                )
            if normalized_provider not in _LEGACY_LLM_PREFIXES and normalized_provider != "openai_compatible":
                raise ValueError(
                    f"Unknown LLM provider '{provider}'. Supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
                )
            if normalized_provider in _LEGACY_LLM_PREFIXES and normalized_provider != "openai_compatible":
                legacy_prefix = normalized_provider
            resolved_model = (model_name or "").strip()

        base_url = (config.llm_base_url or "").strip() or None
        if legacy_prefix == "ollama" and not base_url:
            ollama_url = os.environ.get("MESA_OLLAMA_URL", config.ollama_url)
            base_url = _normalize_ollama_v1_url(ollama_url)
            if not resolved_model:
                resolved_model = "llama3.2:3b"

        if raw_spec is not None and legacy_prefix is not None:
            _warn_legacy_spec_once(raw_spec, resolved_model, base_url)

        external_enabled = bool(getattr(config, "external_provider_enabled", False))
        local_target = base_url is not None and is_local_endpoint(base_url)

        if not external_enabled:
            if legacy_prefix in _EXPLICIT_EXTERNAL_ONLY_PREFIXES:
                raise ValueError(
                    f"External provider '{legacy_prefix}' is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
                )
            if not local_target:
                raise ValueError(
                    "External LLM endpoint is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
                )

        from mesa_memory.adapter.openai_compat import OpenAICompatibleAdapter

        api_key = (config.llm_api_key or "").strip() or (
            "local" if local_target else None
        )
        if not api_key:
            raise ValueError(
                "API key is required for remote LLM endpoint. Set MESA_LLM_API_KEY."
            )

        return OpenAICompatibleAdapter(
            api_key=api_key,
            base_url=base_url,
            model_name=resolved_model,
            timeout_seconds=config.llm_timeout_seconds,
        )

    @staticmethod
    def get_extraction_adapter() -> BaseUniversalLLMAdapter:
        """Compose the frozen, model-configurable fact extraction provider."""
        if not AdapterFactory.is_llm_configured():
            raise LLMNotConfiguredError(
                "no LLM configured. Set MESA_LLM_MODEL to enable extraction."
            )
        return AdapterFactory.get_adapter(
            thinking=config.extraction_thinking,
        )
