"""Tests for Task F010: Hard External Provider Egress Fence."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.adapter.factory import AdapterFactory, reset_legacy_warnings
from mesa_memory.config import config
from mesa_memory.embedding.service import (
    EmbeddingIdentity,
    EmbeddingService,
    ExternalProviderForbiddenError,
    get_embedding_service,
    set_global_embedding_service,
)


def test_egress_fence_blocks_external_llm_adapters(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "llm_base_url", None)

    for provider in ("openai_compatible", "claude", "openai", "anthropic"):
        with pytest.raises(
            ValueError, match="forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false"
        ):
            AdapterFactory.get_adapter(provider)


def test_egress_fence_permits_local_and_mock_adapters(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)

    mock_adapter = AdapterFactory.get_adapter("mock")
    assert mock_adapter is not None

    with monkeypatch.context() as m:
        import mesa_memory.adapter.openai_compat as compat_mod

        m.setattr(
            compat_mod,
            "openai",
            SimpleNamespace(OpenAI=MagicMock(), AsyncOpenAI=MagicMock()),
        )
        ollama_adapter = AdapterFactory.get_adapter("ollama")
        assert ollama_adapter is not None
        assert ollama_adapter.base_url == "http://127.0.0.1:11434/v1"


def test_egress_fence_rejects_remote_ollama(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setenv("MESA_OLLAMA_URL", "https://hosted-ollama.example")
    with pytest.raises(
        ValueError, match="forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false"
    ):
        AdapterFactory.get_adapter("ollama")


def test_extraction_profile_uses_local_openai_compat(monkeypatch):
    reset_legacy_warnings()
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "llm_model", "ollama:qwen3:1.7b")
    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "llm_api_key", None)
    monkeypatch.setattr(config, "extraction_thinking", False)
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)
    with monkeypatch.context() as context:
        import mesa_memory.adapter.openai_compat as compat_mod

        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="extracted"))],
            usage=None,
            model="qwen3:1.7b",
        )
        sdk = SimpleNamespace(
            OpenAI=MagicMock(return_value=client),
            AsyncOpenAI=MagicMock(),
        )
        context.setattr(compat_mod, "openai", sdk)
        adapter = AdapterFactory.get_extraction_adapter()
        assert adapter.complete("extract") == "extracted"

    assert sdk.OpenAI.call_args.kwargs["base_url"] == "http://127.0.0.1:11434/v1"
    assert sdk.OpenAI.call_args.kwargs["api_key"] == "local"
    assert client.chat.completions.create.call_args.kwargs["model"] == "qwen3:1.7b"


def test_egress_fence_blocks_external_embedding_service(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)

    ident = EmbeddingIdentity(
        provider="openai_compatible",
        model="text-embedding-3-small",
        dimension=1536,
    )
    with pytest.raises(
        ExternalProviderForbiddenError, match="MESA_EXTERNAL_PROVIDER_ENABLED=false"
    ):
        EmbeddingService(identity=ident, external_enabled=False)


@pytest.fixture
def model_clients(monkeypatch):
    import mesa_memory.adapter.openai_compat as compat_mod

    sync = MagicMock()
    sync.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="local response"))],
        usage=None,
        model="local-model",
    )
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])
    sync.embeddings.create.return_value = response
    async_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    sdk = SimpleNamespace(
        OpenAI=MagicMock(return_value=sync),
        AsyncOpenAI=MagicMock(return_value=async_client),
    )
    monkeypatch.setattr(compat_mod, "openai", sdk)
    monkeypatch.setitem(sys.modules, "openai", sdk)
    for field in (
        "llm_api_key",
        "embedding_api_key",
        "embedding_base_url",
        "llm_base_url",
    ):
        monkeypatch.setattr(config, field, None)
    monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)
    monkeypatch.setattr(config, "external_provider_enabled", False)
    yield sdk
    set_global_embedding_service(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["llm", "embedding", "direct"])
@pytest.mark.parametrize(
    "provider,url",
    [
        ("openai_compatible", "http://127.0.0.1:8001/v1"),
        ("openai_compatible", "https://localhost/v1"),
        ("openai_compatible", "http://[::1]:8001/v1"),
        ("openai_compatible", "http://host.docker.internal:8001/v1"),
        ("ollama", "http://host.docker.internal:11434"),
    ],
)
async def test_d31_local_models_without_keys(
    monkeypatch, model_clients, provider, url, component
):
    sdk = model_clients
    monkeypatch.setattr(
        config, "llm_base_url", url if provider == "openai_compatible" else None
    )
    monkeypatch.setattr(config, "ollama_url", url)
    expected_url = url if provider == "openai_compatible" else url + "/v1"
    if component == "llm":
        adapter = AdapterFactory.get_adapter(provider, model_name="local-model")
        assert adapter.complete("hello") == "local response"
        assert sdk.OpenAI.call_args.kwargs["api_key"] == "local"
        assert sdk.OpenAI.call_args.kwargs["base_url"] == expected_url
        return
    identity = EmbeddingIdentity(provider=provider, model="local-model", dimension=2)
    if component == "direct":
        direct = EmbeddingService(
            identity=identity,
            external_enabled=False,
            provider_fn=lambda text: [1.0, 0.0],
        )
        assert direct.embed_query("query") == [1.0, 0.0]
        return
    service = get_embedding_service(
        identity=identity, external_enabled=False, force_refresh=True
    )
    assert service.embed_document("document") == [1.0, 0.0]
    assert await service.aembed_query("query") == [1.0, 0.0]
    for client in (sdk.OpenAI, sdk.AsyncOpenAI):
        assert client.call_args.kwargs["base_url"] == expected_url
        assert client.call_args.kwargs["api_key"] == (
            "local" if provider == "openai_compatible" else "ollama"
        )


@pytest.mark.parametrize(
    "provider,url",
    [
        ("openai_compatible", "http://10.0.0.5/v1"),
        ("openai_compatible", "https://api.example.com/v1"),
        ("openai_compatible", "http://localhost.evil/v1"),
        ("openai_compatible", "ftp://localhost/v1"),
        ("ollama", "http://10.0.0.5:11434"),
        ("ollama", "https://api.example.com"),
        *[(p, "http://127.0.0.1/v1") for p in ("openai", "claude", "anthropic", "hosted")],
    ],
)
def test_d31_forbidden_before_client_construction(
    monkeypatch, model_clients, provider, url
):
    sdk = model_clients
    monkeypatch.setattr(config, "llm_base_url", url)
    monkeypatch.setattr(config, "embedding_base_url", url)
    monkeypatch.setattr(config, "ollama_url", url)
    with pytest.raises(ValueError, match="MESA_EXTERNAL_PROVIDER_ENABLED=false"):
        AdapterFactory.get_adapter(provider, model_name="blocked")
    identity = EmbeddingIdentity(provider=provider, model="blocked", dimension=2)
    with pytest.raises(
        (ValueError, ExternalProviderForbiddenError),
        match="MESA_EXTERNAL_PROVIDER_ENABLED=false",
    ):
        get_embedding_service(
            identity=identity, external_enabled=False, force_refresh=True
        )
    with pytest.raises(
        (ValueError, ExternalProviderForbiddenError),
        match="MESA_EXTERNAL_PROVIDER_ENABLED=false",
    ):
        EmbeddingService(identity=identity, external_enabled=False)
    sdk.OpenAI.assert_not_called()
    sdk.AsyncOpenAI.assert_not_called()


def test_d31_external_enabled_retains_remote_keys(monkeypatch, model_clients):
    sdk = model_clients
    monkeypatch.setattr(config, "external_provider_enabled", True)
    monkeypatch.setattr(config, "llm_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(config, "llm_api_key", "configured-key")
    AdapterFactory.get_adapter("openai_compatible", model_name="remote")
    assert sdk.OpenAI.call_args.kwargs["api_key"] == "configured-key"
    identity = EmbeddingIdentity(
        provider="openai_compatible", model="remote", dimension=2
    )
    get_embedding_service(identity=identity, external_enabled=True, force_refresh=True)
    assert sdk.OpenAI.call_args.kwargs["api_key"] == "configured-key"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:8001/v1", True),
        ("https://LOCALHOST/v1", True),
        ("http://127.0.0.1:8001/v1", True),
        ("http://[::1]:8001/v1", True),
        ("http://host.docker.internal:11434", True),
        ("http://localhost.evil/v1", False),
        ("http://localhost@evil.example/v1", False),
        ("http://127.0.0.2/v1", False),
        ("ftp://localhost/v1", False),
        ("localhost:8001/v1", False),
        ("http://[broken", False),
        ("", False),
    ],
)
def test_d31_local_endpoint_definition(url, expected):
    from mesa_memory.adapter.endpoints import is_local_endpoint

    assert is_local_endpoint(url) is expected
