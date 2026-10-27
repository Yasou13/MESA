import pytest

from mesa_memory.adapter.factory import AdapterFactory, DeterministicMockAdapter

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


def test_parse_llm_spec_all_providers():
    from mesa_memory.adapter.factory import parse_llm_spec

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
    assert parse_llm_spec("mock:test-model") == ("mock", "test-model")


def test_parse_llm_spec_preserves_second_colon():
    from mesa_memory.adapter.factory import parse_llm_spec

    # ollama:qwen3:8b must keep qwen3:8b as model
    provider, model = parse_llm_spec("ollama:qwen3:8b")
    assert provider == "ollama"
    assert model == "qwen3:8b"

    # Multiple colons preserved in model name
    provider, model = parse_llm_spec("ollama:ns/repo:tag:extra")
    assert provider == "ollama"
    assert model == "ns/repo:tag:extra"


def test_parse_llm_spec_invalid_rejected():
    from mesa_memory.adapter.factory import parse_llm_spec

    with pytest.raises(ValueError, match="MESA_LLM_MODEL is not set"):
        parse_llm_spec("")

    with pytest.raises(ValueError, match="missing ':' separator"):
        parse_llm_spec("ollama")

    with pytest.raises(ValueError, match="Unknown LLM provider 'unsupported'"):
        parse_llm_spec("unsupported:model")

    with pytest.raises(ValueError, match="Empty model in LLM spec"):
        parse_llm_spec("ollama:")


def test_factory_resolves_all_providers(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import mesa_memory.adapter.claude as claude_mod
    import mesa_memory.adapter.live as live_mod
    import mesa_memory.adapter.ollama as ollama_mod
    from mesa_memory.adapter.claude import ClaudeAdapter
    from mesa_memory.adapter.factory import AdapterFactory, DeterministicMockAdapter
    from mesa_memory.adapter.live import OpenAICompatibleAdapter
    from mesa_memory.adapter.ollama import OllamaAdapter
    from mesa_memory.config import config

    monkeypatch.setattr(ollama_mod, "ollama", SimpleNamespace(Client=MagicMock()))
    monkeypatch.setattr(
        claude_mod,
        "anthropic",
        SimpleNamespace(Anthropic=MagicMock(), AsyncAnthropic=MagicMock()),
    )
    monkeypatch.setattr(
        live_mod,
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

    # Ollama
    monkeypatch.setattr(config, "llm_model", "ollama:qwen3:8b")
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OllamaAdapter)
    assert adapter._model == "qwen3:8b"

    # OpenAI (uses default base URL when base_url is None)
    monkeypatch.setattr(config, "llm_model", "openai:gpt-4o-mini")
    monkeypatch.setattr(config, "llm_base_url", None)
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "gpt-4o-mini"
    assert adapter.base_url is None

    # OpenAI-compatible (requires base URL)
    monkeypatch.setattr(config, "llm_model", "openai_compatible:custom-model")
    monkeypatch.setattr(config, "llm_base_url", None)
    with pytest.raises(ValueError, match="base_url is required"):
        AdapterFactory.get_adapter()

    monkeypatch.setattr(config, "llm_base_url", "https://custom.endpoint/v1")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "custom-model"
    assert adapter.base_url == "https://custom.endpoint/v1"

    # Anthropic
    monkeypatch.setattr(config, "llm_model", "anthropic:claude-sonnet-4-5")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, ClaudeAdapter)
    assert adapter.model_name == "claude-sonnet-4-5"


def test_extraction_on_without_llm_model_fails(monkeypatch):
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.config import config

    monkeypatch.setattr(config, "llm_model", "")
    with pytest.raises(ValueError, match="MESA_LLM_MODEL is not set"):
        AdapterFactory.get_extraction_adapter()

    with pytest.raises(ValueError, match="MESA_LLM_MODEL is not set"):
        AdapterFactory.get_adapter()


def test_unprefixed_llm_base_url_in_env_changes_nothing(monkeypatch):
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.config import MesaConfig, config

    monkeypatch.setenv("LLM_BASE_URL", "https://law-answering-llm.example.com/v1")
    cfg = MesaConfig()
    assert cfg.llm_base_url is None

    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "llm_model", "openai_compatible:model")
    monkeypatch.setattr(config, "llm_api_key", "test-key")
    monkeypatch.setattr(config, "external_provider_enabled", True)
    # Even though LLM_BASE_URL is set in environment, openai_compatible still fails because MESA_LLM_BASE_URL is unset
    with pytest.raises(ValueError, match="base_url is required for 'openai_compatible' provider"):
        AdapterFactory.get_adapter()


def test_openai_provider_fails_when_mesa_llm_api_key_unset_even_if_outside_openai_api_key_is_set(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import mesa_memory.adapter.claude as claude_mod
    import mesa_memory.adapter.live as live_mod
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.config import config

    mock_openai = MagicMock()
    mock_anthropic = MagicMock()
    monkeypatch.setattr(live_mod, "openai", SimpleNamespace(OpenAI=mock_openai, AsyncOpenAI=MagicMock()))
    monkeypatch.setattr(claude_mod, "anthropic", SimpleNamespace(Anthropic=mock_anthropic, AsyncAnthropic=MagicMock()))
    monkeypatch.setattr(config, "external_provider_enabled", True)
    monkeypatch.setattr(config, "llm_api_key", None)
    monkeypatch.setenv("OPENAI_API_KEY", "outside-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "outside-anthropic-secret")

    # openai:
    monkeypatch.setattr(config, "llm_model", "openai:gpt-4o-mini")
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()
    mock_openai.assert_not_called()

    # anthropic:
    monkeypatch.setattr(config, "llm_model", "anthropic:claude-sonnet-4-5")
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()
    mock_anthropic.assert_not_called()

    # openai_compatible:
    monkeypatch.setattr(config, "llm_model", "openai_compatible:custom-model")
    monkeypatch.setattr(config, "llm_base_url", "https://custom.endpoint/v1")
    with pytest.raises(ValueError, match="MESA_LLM_API_KEY"):
        AdapterFactory.get_adapter()
    mock_openai.assert_not_called()


def test_factory_invalid_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider 'invalid'"):
        AdapterFactory.get_adapter(provider="invalid")
