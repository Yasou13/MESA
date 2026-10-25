"""
MESA Adapter Factory — Provider selection via MESA_LLM_MODEL=provider:model.

Supported providers:
  - ollama
  - openai
  - openai_compatible
  - anthropic
  - mock
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config

logger = logging.getLogger("MESA_AdapterFactory")

SUPPORTED_LLM_PROVIDERS: tuple[str, ...] = (
    "ollama",
    "openai",
    "openai_compatible",
    "anthropic",
    "mock",
)


def parse_llm_spec(spec: str) -> tuple[str, str]:
    """Parse 'provider:model' string into (provider, model).

    Splits at the first colon so model names containing colons (e.g. 'qwen3:8b')
    preserve their tags.
    """
    if not spec or not spec.strip():
        raise ValueError(
            f"MESA_LLM_MODEL is not set. Set MESA_LLM_MODEL=provider:model "
            f"(supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)})."
        )
    parts = spec.strip().split(":", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid LLM spec '{spec}': missing ':' separator. Format must be 'provider:model' "
            f"(supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)})."
        )
    provider = parts[0].strip().lower()
    model = parts[1].strip()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. Supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )
    if not model:
        raise ValueError(
            f"Empty model in LLM spec '{spec}'. Format must be 'provider:model' "
            f"(supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)})."
        )
    return provider, model


class DeterministicMockAdapter(BaseUniversalLLMAdapter):
    """Zero-dependency mock adapter for testing and CI environments.

    Returns deterministic, non-random outputs.  Should NEVER be used in
    production — emits a CRITICAL warning on construction.
    """

    def __init__(self):  # type: ignore[no-untyped-def]
        logger.critical(
            "DeterministicMockAdapter active — no real LLM provider detected. "
            "Set MESA_OLLAMA_URL, OPENAI_API_KEY, or ANTHROPIC_API_KEY to "
            "enable a real provider."
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
    def _require_permitted_ollama_url(url: str) -> None:
        """Treat Ollama as local only when its endpoint is actually loopback."""
        if getattr(config, "external_provider_enabled", False):
            return
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                "Remote Ollama is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
            )

    @staticmethod
    def get_adapter(
        provider: Optional[str] = None,
        *,
        model_name: str | None = None,
        thinking: bool | None = None,
    ) -> BaseUniversalLLMAdapter:
        if provider is None:
            if not config.llm_model:
                raise ValueError(
                    "MESA_LLM_MODEL is not set. Set MESA_LLM_MODEL=provider:model "
                    f"(supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)})."
                )
            provider, default_model = parse_llm_spec(config.llm_model)
            if model_name is None:
                model_name = default_model

        normalized_provider = provider.strip().lower()
        external_providers = {"openai_compatible", "claude", "openai", "anthropic"}
        if (
            not getattr(config, "external_provider_enabled", False)
            and normalized_provider in external_providers
        ):
            raise ValueError(
                f"External provider '{provider}' is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
            )

        # ── Explicit provider selection ──────────────────────────────────
        if normalized_provider == "openai_compatible":
            from mesa_memory.adapter.live import OpenAICompatibleAdapter

            if not config.llm_base_url:
                raise ValueError(
                    "base_url is required for 'openai_compatible' provider. Set MESA_LLM_BASE_URL."
                )
            api_key = config.llm_api_key or os.environ.get("OPENAI_API_KEY")
            return OpenAICompatibleAdapter(
                api_key=api_key,
                base_url=config.llm_base_url,
                model_name=model_name,
                embedding_model_name=config.llm_embedding_model_name,
                timeout_seconds=config.llm_timeout_seconds,
            )
        elif normalized_provider == "openai":
            from mesa_memory.adapter.live import OpenAICompatibleAdapter

            api_key = config.llm_api_key or os.environ.get("OPENAI_API_KEY")
            return OpenAICompatibleAdapter(
                api_key=api_key,
                base_url=config.llm_base_url,
                model_name=model_name,
                embedding_model_name=config.llm_embedding_model_name,
                timeout_seconds=config.llm_timeout_seconds,
            )
        elif normalized_provider in {"claude", "anthropic"}:
            from mesa_memory.adapter.claude import ClaudeAdapter

            api_key = config.llm_api_key or os.environ.get("ANTHROPIC_API_KEY")
            return ClaudeAdapter(
                anthropic_api_key=api_key,
                model_name=model_name,
            )
        elif normalized_provider == "ollama":
            from mesa_memory.adapter.ollama import OllamaAdapter

            ollama_url = os.environ.get("MESA_OLLAMA_URL", config.ollama_url)
            AdapterFactory._require_permitted_ollama_url(ollama_url)
            return OllamaAdapter(
                model=model_name or "llama3.2:3b",
                base_url=ollama_url,
                thinking=bool(thinking),
                timeout_seconds=config.ollama_timeout_seconds,
                num_ctx=config.ollama_num_ctx,
            )
        elif normalized_provider == "mock":
            return DeterministicMockAdapter()

        raise ValueError(
            f"Unknown LLM provider '{provider}'. Supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )

    @staticmethod
    def get_extraction_adapter() -> BaseUniversalLLMAdapter:
        """Compose the frozen, model-configurable fact extraction provider."""
        if not config.llm_model:
            raise ValueError(
                "MESA_LLM_MODEL is not set. Set MESA_LLM_MODEL=provider:model "
                f"(supported providers: {', '.join(SUPPORTED_LLM_PROVIDERS)}) "
                "to enable extraction."
            )
        return AdapterFactory.get_adapter(
            thinking=config.extraction_thinking,
        )

    @staticmethod
    def get_validation_adapters(
        mode: int,
    ) -> tuple[BaseUniversalLLMAdapter, ...]:
        """Build validator adapters according to the selected validation mode.

        Mode 0: returns empty tuple () - zero validation adapters.
        Mode 1: returns (adapter_a,) - exactly one validation adapter.
        Mode 2: returns (adapter_a, adapter_b) - two distinct validation adapters.
        """
        if mode == 0:
            return ()
        elif mode == 1:
            if not config.tier3_llm_provider_a or not config.tier3_llm_model_name_a:
                raise ValueError(
                    "Mode 1 validation requires provider and model for adapter A"
                )
            return (
                AdapterFactory.get_adapter(
                    config.tier3_llm_provider_a,
                    model_name=config.tier3_llm_model_name_a,
                ),
            )
        elif mode == 2:
            return AdapterFactory.get_tier3_adapters()
        else:
            raise ValueError(f"Invalid validation mode: {mode}")

    @staticmethod
    def get_tier3_adapters() -> tuple[BaseUniversalLLMAdapter, BaseUniversalLLMAdapter]:
        """Build the two independently configured Tier-3 validators.

        Tier-3 is a consensus control, not a duplicated call to one model.
        Missing or equal provider/model pairs therefore fail startup closed.
        """
        specs = (
            (config.tier3_llm_provider_a, config.tier3_llm_model_name_a),
            (config.tier3_llm_provider_b, config.tier3_llm_model_name_b),
        )
        if not all(provider and model for provider, model in specs):
            raise ValueError("Tier-3 requires provider and model for both adapters")
        normalized = tuple((provider.casefold(), model.casefold()) for provider, model in specs)  # type: ignore[union-attr]
        if normalized[0] == normalized[1]:
            raise ValueError("Tier-3 adapters must use different provider/model pairs")
        return (
            AdapterFactory.get_adapter(specs[0][0], model_name=specs[0][1]),
            AdapterFactory.get_adapter(specs[1][0], model_name=specs[1][1]),
        )
