import os
from mesa_memory.config import config
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.adapter.live import OpenAICompatibleAdapter

class AdapterFactory:
    @staticmethod
    def get_adapter() -> BaseUniversalLLMAdapter:
        provider = config.mesa_llm_provider
        if provider == "openai_compatible":
            return OpenAICompatibleAdapter(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
                model_name=config.llm_model_name
            )
        raise ValueError(f"Unknown LLM provider: {provider}")
