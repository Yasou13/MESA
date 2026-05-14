import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field

class TestConfig(BaseSettings):
    model_config = ConfigDict(env_prefix="MESA_", env_file=".env", extra="ignore")

    mesa_llm_provider: str = Field("openai_compatible", validation_alias="MESA_LLM_PROVIDER")
    llm_base_url: str | None = Field(None, validation_alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(None, validation_alias="LLM_API_KEY")
    llm_model_name: str | None = Field(None, validation_alias="LLM_MODEL_NAME")

config = TestConfig()
print(config.model_dump())
