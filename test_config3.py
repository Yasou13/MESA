import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class TestConfig(BaseSettings):
    model_config = ConfigDict(env_prefix="MESA_", env_file=".env", extra="forbid")

    llm_provider: str = "claude"
    mesa_llm_provider: str = "openai_compatible"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model_name: str | None = None

try:
    config = TestConfig()
    print("Success")
except Exception as e:
    print("Failed:", e)
