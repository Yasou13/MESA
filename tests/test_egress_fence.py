"""Tests for Task F010: Hard External Provider Egress Fence."""

from unittest.mock import MagicMock

import pytest

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import config
from mesa_memory.embedding.service import (
    EmbeddingIdentity,
    EmbeddingService,
    ExternalProviderForbiddenError,
)


def test_egress_fence_blocks_external_llm_adapters(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)

    for provider in ("openai_compatible", "claude", "openai", "anthropic"):
        with pytest.raises(
            ValueError, match="forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false"
        ):
            AdapterFactory.get_adapter(provider)


def test_egress_fence_permits_local_and_mock_adapters(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)

    mock_adapter = AdapterFactory.get_adapter("mock")
    assert mock_adapter is not None

    with monkeypatch.context() as m:
        import mesa_memory.adapter.ollama as ollama_mod

        m.setattr(ollama_mod, "ollama", MagicMock())
        ollama_adapter = AdapterFactory.get_adapter("ollama")
        assert ollama_adapter is not None


def test_egress_fence_rejects_remote_ollama(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setenv("MESA_OLLAMA_URL", "https://hosted-ollama.example")
    with pytest.raises(ValueError, match="Remote Ollama is forbidden"):
        AdapterFactory.get_adapter("ollama")


def test_extraction_profile_is_local_qwen_without_thinking(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "extraction_provider", "ollama")
    monkeypatch.setattr(config, "extraction_model", "qwen3:1.7b")
    monkeypatch.setattr(config, "extraction_thinking", False)
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)
    with monkeypatch.context() as context:
        import mesa_memory.adapter.ollama as ollama_mod

        client = MagicMock()
        context.setattr(
            ollama_mod, "ollama", MagicMock(Client=MagicMock(return_value=client))
        )
        adapter = AdapterFactory.get_extraction_adapter()
        adapter.complete("extract")

    client.generate.assert_called_once_with(
        model="qwen3:1.7b", prompt="extract", think=False
    )


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


def test_egress_fence_mode_2_fails_closed_without_permitted_validators(monkeypatch):
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setattr(config, "tier3_llm_provider_a", "openai_compatible")
    monkeypatch.setattr(config, "tier3_llm_model_name_a", "gpt-4o-mini")
    monkeypatch.setattr(config, "tier3_llm_provider_b", "claude")
    monkeypatch.setattr(config, "tier3_llm_model_name_b", "claude-3-haiku")

    with pytest.raises(
        ValueError, match="forbidden when MESA_EXTERNAL_PROVIDER_ENABLED=false"
    ):
        AdapterFactory.get_validation_adapters(2)
