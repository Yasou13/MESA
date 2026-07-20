from unittest.mock import patch

import pytest

from mesa_memory.adapter.claude import ClaudeAdapter
from mesa_memory.adapter.factory import AdapterFactory, DeterministicMockAdapter
from mesa_memory.adapter.live import OpenAICompatibleAdapter

pytestmark = pytest.mark.optional_provider


def test_deterministic_mock_adapter():
    adapter = DeterministicMockAdapter()

    res = adapter.complete("prompt")
    assert res == "[MOCK] Deterministic response"

    embed = adapter.embed("text")
    assert isinstance(embed, list)

    batch = adapter.embed_batch(["text1"])
    assert len(batch) == 1

    assert adapter.get_token_count("hello world") == 2


@pytest.mark.asyncio
async def test_deterministic_mock_adapter_async():
    adapter = DeterministicMockAdapter()
    res = await adapter.acomplete("prompt")
    assert res == "[MOCK] Deterministic response"

    embed = await adapter.aembed("test")
    assert isinstance(embed, list)

    batch = await adapter.aembed_batch(["text1"])
    assert len(batch) == 1


@patch("os.environ.get")
@patch("mesa_memory.adapter.factory.config")
def test_factory_auto_detect_fallback(mock_config, mock_env_get):
    mock_env_get.return_value = None
    mock_config.llm_api_key = "your_groq_api_key_here"
    mock_config.anthropic_api_key = ""
    mock_config.mesa_llm_provider = "auto"

    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, DeterministicMockAdapter)


@patch("os.environ.get")
@patch("mesa_memory.adapter.factory.config")
def test_factory_auto_detect_openai(mock_config, mock_env_get):
    def env_get_side_effect(key, default=None):
        if key == "OPENAI_API_KEY":
            return "sk-test-openai"
        return default

    mock_env_get.side_effect = env_get_side_effect
    mock_config.mesa_llm_provider = "auto"
    mock_config.llm_api_key = ""
    mock_config.llm_base_url = None
    mock_config.llm_model_name = None

    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)


@patch("os.environ.get")
@patch("mesa_memory.adapter.factory.config")
def test_factory_auto_detect_claude(mock_config, mock_env_get):
    def env_get_side_effect(key, default=None):
        if key == "ANTHROPIC_API_KEY":
            return "sk-test-anthropic"
        return default

    mock_env_get.side_effect = env_get_side_effect
    mock_config.mesa_llm_provider = "auto"
    mock_config.llm_api_key = ""
    mock_config.anthropic_api_key = "sk-test-anthropic"

    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, ClaudeAdapter)


def test_factory_invalid_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider: invalid"):
        AdapterFactory.get_adapter(provider="invalid")
