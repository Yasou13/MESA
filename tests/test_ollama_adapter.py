"""Tests for OllamaAdapter timeout and generation options (P3-01 / D15)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from mesa_memory.adapter import factory as factory_mod
from mesa_memory.adapter import ollama as ollama_mod
from mesa_memory.config import MesaConfig, config


class DummyExtractionSchema(BaseModel):
    subject: str
    predicate: str
    object: str


def test_ollama_client_receives_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """OllamaAdapter builds ollama.Client with timeout and optional host."""
    recorded_kwargs: list[dict] = []

    def fake_client(**kwargs):
        recorded_kwargs.append(kwargs)
        return MagicMock()

    monkeypatch.setattr(ollama_mod, "ollama", SimpleNamespace(Client=fake_client))

    # Default timeout and no base_url
    ollama_mod.OllamaAdapter()
    assert recorded_kwargs[-1] == {"timeout": 300.0}

    # Custom timeout and custom base_url
    ollama_mod.OllamaAdapter(
        base_url="http://127.0.0.1:11434",
        timeout_seconds=120.5,
        num_ctx=4096,
    )
    assert recorded_kwargs[-1] == {
        "timeout": 120.5,
        "host": "http://127.0.0.1:11434",
    }


def test_ollama_complete_plain_call_sends_num_ctx_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain complete() call passes options with num_ctx only."""
    client = MagicMock()
    client.generate.return_value = {"response": "simple response"}
    monkeypatch.setattr(
        ollama_mod, "ollama", SimpleNamespace(Client=MagicMock(return_value=client))
    )

    adapter = ollama_mod.OllamaAdapter(num_ctx=8192)
    response = adapter.complete("plain prompt")

    assert response == "simple response"
    client.generate.assert_called_once()
    _, kwargs = client.generate.call_args
    assert kwargs["options"] == {"num_ctx": 8192}
    assert "temperature" not in kwargs["options"]
    assert "num_predict" not in kwargs["options"]


def test_ollama_complete_schema_call_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema call with max_tokens sends num_ctx, temperature 0 and num_predict."""
    client = MagicMock()
    client.generate.return_value = {
        "response": '{"subject": "A", "predicate": "rel", "object": "B"}'
    }
    monkeypatch.setattr(
        ollama_mod, "ollama", SimpleNamespace(Client=MagicMock(return_value=client))
    )

    adapter = ollama_mod.OllamaAdapter(num_ctx=8192)

    # Schema call with max_tokens (e.g. from FactExtractionService)
    result = adapter.complete(
        "structured prompt",
        schema=DummyExtractionSchema,
        max_tokens=512,
    )

    assert isinstance(result, DummyExtractionSchema)
    assert result.subject == "A"
    client.generate.assert_called_once()
    _, kwargs = client.generate.call_args
    assert kwargs["options"] == {
        "num_ctx": 8192,
        "temperature": 0,
        "num_predict": 512,
    }


def test_ollama_complete_schema_without_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema call without max_tokens sends num_ctx and temperature 0."""
    client = MagicMock()
    client.generate.return_value = {
        "response": '{"subject": "X", "predicate": "is", "object": "Y"}'
    }
    monkeypatch.setattr(
        ollama_mod, "ollama", SimpleNamespace(Client=MagicMock(return_value=client))
    )

    adapter = ollama_mod.OllamaAdapter(num_ctx=4096)
    result = adapter.complete("prompt", schema=DummyExtractionSchema)

    assert isinstance(result, DummyExtractionSchema)
    client.generate.assert_called_once()
    _, kwargs = client.generate.call_args
    assert kwargs["options"] == {"num_ctx": 4096, "temperature": 0}
    assert "num_predict" not in kwargs["options"]


def test_ollama_complete_plain_call_with_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain call with max_tokens sends num_ctx and num_predict without temperature."""
    client = MagicMock()
    client.generate.return_value = {"response": "short plain"}
    monkeypatch.setattr(
        ollama_mod, "ollama", SimpleNamespace(Client=MagicMock(return_value=client))
    )

    adapter = ollama_mod.OllamaAdapter(num_ctx=8192)
    response = adapter.complete("prompt", max_tokens=100)

    assert response == "short plain"
    client.generate.assert_called_once()
    _, kwargs = client.generate.call_args
    assert kwargs["options"] == {"num_ctx": 8192, "num_predict": 100}
    assert "temperature" not in kwargs["options"]


def test_adapter_factory_passes_config_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """AdapterFactory passes config timeout and num_ctx to OllamaAdapter."""
    client = MagicMock()
    monkeypatch.setattr(
        ollama_mod, "ollama", SimpleNamespace(Client=MagicMock(return_value=client))
    )
    monkeypatch.setattr(config, "ollama_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(config, "ollama_timeout_seconds", 180.0)
    monkeypatch.setattr(config, "ollama_num_ctx", 4096)

    # 1. Explicit provider
    adapter1 = factory_mod.AdapterFactory.get_adapter(provider="ollama")
    assert isinstance(adapter1, ollama_mod.OllamaAdapter)
    assert adapter1.timeout_seconds == 180.0
    assert adapter1.num_ctx == 4096

    # 2. Configured MESA_LLM_MODEL
    monkeypatch.setattr(config, "llm_model", "ollama:qwen3:8b")
    adapter2 = factory_mod.AdapterFactory.get_adapter()
    assert isinstance(adapter2, ollama_mod.OllamaAdapter)
    assert adapter2.timeout_seconds == 180.0
    assert adapter2.num_ctx == 4096


def test_config_ollama_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """MesaConfig defines ollama_timeout_seconds and ollama_num_ctx with expected defaults."""
    fresh_config = MesaConfig()
    assert fresh_config.ollama_timeout_seconds == 300.0
    assert fresh_config.ollama_num_ctx == 8192

    monkeypatch.setenv("MESA_OLLAMA_TIMEOUT_SECONDS", "45.5")
    monkeypatch.setenv("MESA_OLLAMA_NUM_CTX", "16384")
    custom_config = MesaConfig()
    assert custom_config.ollama_timeout_seconds == 45.5
    assert custom_config.ollama_num_ctx == 16384
