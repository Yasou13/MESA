"""
MESA Adapter Factory — Auto-detecting LLM provider selection.

Resolution order:
  1. Explicit ``provider`` argument
  2. ``MESA_LLM_PROVIDER`` env var (via config)
  3. Auto-detection waterfall:
       a. MESA_OLLAMA_URL set       → OllamaAdapter
       b. OPENAI_API_KEY set        → OpenAICompatibleAdapter
       c. ANTHROPIC_API_KEY set     → ClaudeAdapter
       d. Fallback                  → DeterministicMockAdapter (with warning)
"""

import logging
import os
from typing import Optional

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config

logger = logging.getLogger("MESA_AdapterFactory")


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
            return schema.model_validate_json('{"results": []}')
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
    def get_adapter(
        provider: Optional[str] = None, *, model_name: str | None = None
    ) -> BaseUniversalLLMAdapter:
        provider = provider or config.mesa_llm_provider
        selected_model = model_name or config.llm_model_name

        external_providers = {"openai_compatible", "claude", "openai", "anthropic"}
        if (
            not getattr(config, "external_provider_enabled", False)
            and provider.lower() in external_providers
        ):
            raise ValueError(
                f"External provider '{provider}' is forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false."
            )

        # ── Explicit provider selection ──────────────────────────────────
        if provider == "openai_compatible":
            from mesa_memory.adapter.live import OpenAICompatibleAdapter

            return OpenAICompatibleAdapter(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
                model_name=selected_model,
                embedding_model_name=config.llm_embedding_model_name,
                timeout_seconds=config.llm_timeout_seconds,
            )
        elif provider == "claude":
            from mesa_memory.adapter.claude import ClaudeAdapter

            return ClaudeAdapter(
                anthropic_api_key=config.llm_api_key,
                model_name=selected_model,
            )
        elif provider == "ollama":
            from mesa_memory.adapter.ollama import OllamaAdapter

            ollama_url = os.environ.get("MESA_OLLAMA_URL", "http://localhost:11434")
            return OllamaAdapter(
                model=selected_model or "llama3.2:3b",
                base_url=ollama_url,
            )

        # ── Auto-detection waterfall ─────────────────────────────────────
        elif provider == "auto":
            return AdapterFactory._auto_detect()
        elif provider == "mock":
            return DeterministicMockAdapter()

        raise ValueError(f"Unknown LLM provider: {provider}")

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

    @staticmethod
    def _auto_detect() -> BaseUniversalLLMAdapter:
        """Walk the detection waterfall and return the best available adapter."""

        # 1. Ollama (zero-cost local)
        ollama_url = os.environ.get("MESA_OLLAMA_URL")
        if ollama_url:
            logger.info("Auto-detected MESA_OLLAMA_URL → OllamaAdapter")
            from mesa_memory.adapter.ollama import OllamaAdapter

            return OllamaAdapter(
                model=config.llm_model_name or "qwen3:8b",
                base_url=ollama_url,
            )

        if not getattr(config, "external_provider_enabled", False):
            logger.info(
                "External providers disabled; auto-detecting DeterministicMockAdapter."
            )
            return DeterministicMockAdapter()

        # 2. OpenAI-compatible (Groq, OpenAI, Together, etc.)
        openai_key = os.environ.get("OPENAI_API_KEY") or config.llm_api_key
        if openai_key and openai_key not in (
            "your_groq_api_key_here",
            "your-secret-key",
            "",
        ):
            logger.info("Auto-detected OPENAI_API_KEY → OpenAICompatibleAdapter")
            from mesa_memory.adapter.live import OpenAICompatibleAdapter

            return OpenAICompatibleAdapter(
                api_key=openai_key,
                base_url=config.llm_base_url,
                model_name=config.llm_model_name,
                embedding_model_name=config.llm_embedding_model_name,
                timeout_seconds=config.llm_timeout_seconds,
            )

        # 3. Anthropic Claude
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or config.anthropic_api_key
        if anthropic_key and anthropic_key not in ("", "your_anthropic_key_here"):
            logger.info("Auto-detected ANTHROPIC_API_KEY → ClaudeAdapter")
            from mesa_memory.adapter.claude import ClaudeAdapter

            return ClaudeAdapter(anthropic_api_key=anthropic_key)

        # 4. Fallback: DeterministicMockAdapter
        logger.warning(
            "No LLM provider credentials found. Falling back to "
            "DeterministicMockAdapter. Set MESA_OLLAMA_URL for zero-cost mode."
        )
        return DeterministicMockAdapter()
