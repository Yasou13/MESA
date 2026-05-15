from mesa_memory.config import config
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.adapter.live import OpenAICompatibleAdapter


class AdapterFactory:
    @staticmethod
    def get_adapter(provider: str = None) -> BaseUniversalLLMAdapter:
        provider = provider or config.mesa_llm_provider
        
        if provider == "openai_compatible":
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
            return OllamaAdapter(
                model=config.llm_model_name or "mistral",
            )
        elif provider == "mock":
            from mesa_memory.adapter.mock import DeterministicMockAdapter
            return DeterministicMockAdapter()

        raise ValueError(f"Unknown LLM provider: {provider}")
