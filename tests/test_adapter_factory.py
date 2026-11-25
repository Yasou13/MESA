import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mesa_memory.adapter.base import LLMNotConfiguredError
from mesa_memory.adapter.factory import (
    AdapterFactory,
    DeterministicMockAdapter,
    parse_llm_spec,
    reset_legacy_warnings,
)


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


def test_parse_llm_spec_plain_and_legacy():
    assert parse_llm_spec("gpt-4o-mini") == ("openai_compatible", "gpt-4o-mini")
    assert parse_llm_spec("qwen3:8b") == ("openai_compatible", "qwen3:8b")
    assert parse_llm_spec("ollama:qwen3:8b") == ("ollama", "qwen3:8b")
    assert parse_llm_spec("openai:gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert parse_llm_spec("openai_compatible:my-custom-model") == (
        "openai_compatible",
        "my-custom-model",
    )
    assert parse_llm_spec("anthropic:claude-sonnet-4-5") == (
        "anthropic",
        "claude-sonnet-4-5",
    )
    assert parse_llm_spec("mock") == ("mock", "mock")
    assert parse_llm_spec("mock:test-model") == ("mock", "test-model")


def test_parse_llm_spec_preserves_second_colon():
    provider, model = parse_llm_spec("ollama:qwen3:8b")
    assert provider == "ollama"
    assert model == "qwen3:8b"

    provider, model = parse_llm_spec("ollama:ns/repo:tag:extra")
    assert provider == "ollama"
    assert model == "ns/repo:tag:extra"

    provider, model = parse_llm_spec("ns/repo:tag:extra")
    assert provider == "openai_compatible"
    assert model == "ns/repo:tag:extra"


def test_parse_llm_spec_invalid_rejected():
    with pytest.raises(LLMNotConfiguredError, match="no LLM configured"):
        parse_llm_spec("")

    with pytest.raises(ValueError, match="Empty model in LLM spec"):
        parse_llm_spec("ollama:")


def test_factory_resolves_single_openai_compat_adapter(monkeypatch, caplog):
    import mesa_memory.adapter.openai_compat as compat_mod
    from mesa_memory.adapter.openai_compat import OpenAICompatibleAdapter
    from mesa_memory.config import config

    reset_legacy_warnings()
    monkeypatch.setattr(
        compat_mod,
        "openai",
        SimpleNamespace(OpenAI=MagicMock(), AsyncOpenAI=MagicMock()),
    )
    monkeypatch.setattr(config, "external_provider_enabled", True)
    monkeypatch.setattr(config, "llm_api_key", "test-api-key")
    monkeypatch.setattr(config, "llm_base_url", "https://api.openai.com/v1")

    # Mock
    monkeypatch.setattr(config, "llm_model", "mock:deterministic")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, DeterministicMockAdapter)

    # Plain model name with custom base_url — no deprecation warning
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="MESA_AdapterFactory"):
        monkeypatch.setattr(config, "llm_model", "qwen3:8b")
        monkeypatch.setattr(config, "llm_base_url", "http://127.0.0.1:11434/v1")
        adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "qwen3:8b"
    assert adapter.base_url == "http://127.0.0.1:11434/v1"
    assert not [r for r in caplog.records if "deprecated" in r.getMessage()]

    # Legacy ollama: without base_url defaults to local Ollama /v1 and warns once
    caplog.clear()
    monkeypatch.setattr(config, "llm_model", "ollama:qwen3:8b")
    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)
    with caplog.at_level(logging.WARNING, logger="MESA_AdapterFactory"):
        adapter1 = AdapterFactory.get_adapter()
        adapter2 = AdapterFactory.get_adapter()
    assert isinstance(adapter1, OpenAICompatibleAdapter)
    assert isinstance(adapter2, OpenAICompatibleAdapter)
    assert adapter1.model_name == "qwen3:8b"
    assert adapter1.base_url == "http://127.0.0.1:11434/v1"
    warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(warnings) == 1

    # Legacy openai: strips prefix and uses default OpenAI base_url (None)
    monkeypatch.setattr(config, "llm_model", "openai:gpt-4o-mini")
    monkeypatch.setattr(config, "llm_base_url", None)
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "gpt-4o-mini"
    assert adapter.base_url is None

    # Legacy anthropic: strips prefix and uses OpenAI-compatible adapter
    monkeypatch.setattr(config, "llm_model", "anthropic:claude-sonnet-4-5")
    monkeypatch.setattr(config, "llm_base_url", "https://openrouter.ai/api/v1")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "claude-sonnet-4-5"
    assert adapter.base_url == "https://openrouter.ai/api/v1"


def test_extraction_on_without_llm_model_fails(monkeypatch):
    from mesa_memory.config import config

    monkeypatch.setattr(config, "llm_model", "")
    with pytest.raises(LLMNotConfiguredError, match="no LLM configured"):
        AdapterFactory.get_extraction_adapter()

    with pytest.raises(LLMNotConfiguredError, match="no LLM configured"):
        AdapterFactory.get_adapter()


def test_unprefixed_llm_base_url_in_env_changes_nothing(monkeypatch):
    from mesa_memory.config import MesaConfig, config

    monkeypatch.setenv("LLM_BASE_URL", "https://law-answering-llm.example.com/v1")
    cfg = MesaConfig()
    assert cfg.llm_base_url is None

    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "llm_model", "my-model")
    monkeypatch.setattr(config, "llm_api_key", None)
    monkeypatch.setattr(config, "external_provider_enabled", True)
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()


def test_remote_llm_fails_when_mesa_llm_api_key_unset_even_if_outside_keys_set(
    monkeypatch,
):
    import mesa_memory.adapter.openai_compat as compat_mod
    from mesa_memory.config import config

    mock_openai = MagicMock()
    monkeypatch.setattr(
        compat_mod,
        "openai",
        SimpleNamespace(OpenAI=mock_openai, AsyncOpenAI=MagicMock()),
    )
    monkeypatch.setattr(config, "external_provider_enabled", True)
    monkeypatch.setattr(config, "llm_api_key", None)
    monkeypatch.setenv("OPENAI_API_KEY", "outside-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "outside-anthropic-secret")

    # Default OpenAI endpoint without MESA_LLM_API_KEY
    monkeypatch.setattr(config, "llm_model", "gpt-4o-mini")
    monkeypatch.setattr(config, "llm_base_url", None)
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()
    mock_openai.assert_not_called()

    # Remote custom endpoint without MESA_LLM_API_KEY
    monkeypatch.setattr(config, "llm_model", "custom-model")
    monkeypatch.setattr(config, "llm_base_url", "https://custom.endpoint/v1")
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()
    mock_openai.assert_not_called()


def test_factory_invalid_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider 'invalid'"):
        AdapterFactory.get_adapter(provider="invalid")
