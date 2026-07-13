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

    def __init__(self):
        logger.critical(
            "DeterministicMockAdapter active — no real LLM provider detected. "
            "Set MESA_OLLAMA_URL, OPENAI_API_KEY, or ANTHROPIC_API_KEY to "
            "enable a real provider."
        )

    def complete(self, prompt, schema=None, **kwargs):
        text = "[MOCK] Deterministic response"
        if schema is not None:
            return schema.model_validate_json('{"results": []}')
        return text

    async def acomplete(self, prompt, schema=None, **kwargs):
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text, **kwargs):
        return [0.0] * config.embedding_dimension

    async def aembed(self, text, **kwargs):
        return self.embed(text, **kwargs)

    def embed_batch(self, texts, **kwargs):
        return [self.embed(t) for t in texts]

    async def aembed_batch(self, texts, **kwargs):
        return self.embed_batch(texts, **kwargs)

    def get_token_count(self, text):
        return len(text.split())


class AdapterFactory:
    @staticmethod
    def get_adapter(provider: Optional[str] = None) -> BaseUniversalLLMAdapter:
        provider = provider or config.mesa_llm_provider

        # ── Explicit provider selection ──────────────────────────────────
        if provider == "openai_compatible":
            from mesa_memory.adapter.live import OpenAICompatibleAdapter

            return OpenAICompatibleAdapter(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
                model_name=config.llm_model_name,
            )
        elif provider == "claude":
            from mesa_memory.adapter.claude import ClaudeAdapter

            return ClaudeAdapter(
                anthropic_api_key=config.llm_api_key,
            )
        elif provider == "ollama":
            from mesa_memory.adapter.ollama import OllamaAdapter

            ollama_url = os.environ.get("MESA_OLLAMA_URL", "http://localhost:11434")
            return OllamaAdapter(
                model=config.llm_model_name or "llama3.2:3b",
                base_url=ollama_url,
            )

        # ── Auto-detection waterfall ─────────────────────────────────────
        elif provider == "auto":
            return AdapterFactory._auto_detect()

        raise ValueError(f"Unknown LLM provider: {provider}")

    @staticmethod
    def _auto_detect() -> BaseUniversalLLMAdapter:
        """Walk the detection waterfall and return the best available adapter."""

        # 1. Ollama (zero-cost local)
        ollama_url = os.environ.get("MESA_OLLAMA_URL")
        if ollama_url:
            logger.info("Auto-detected MESA_OLLAMA_URL=%s → OllamaAdapter", ollama_url)
            from mesa_memory.adapter.ollama import OllamaAdapter

            return OllamaAdapter(
                model=config.llm_model_name or "qwen3:8b",
                base_url=ollama_url,
            )

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
