"""Tests for tokenizer helpers and zero-cost OpenAI-compatible factory wiring."""

import pytest


def test_count_tokens_openai():
    from mesa_memory.adapter.tokenizer import count_tokens

    count = count_tokens("Hello world, this is a test.", adapter_type="openai")
    assert isinstance(count, int)
    assert count > 0


def test_count_tokens_claude():
    from mesa_memory.adapter.tokenizer import count_tokens

    count = count_tokens("Hello world, this is a test.", adapter_type="claude")
    assert isinstance(count, int)
    assert count > 0


def test_count_tokens_ollama():
    from mesa_memory.adapter.tokenizer import count_tokens

    count = count_tokens(
        "Hello world, this is a test.", adapter_type="ollama", model_id="nonexistent-model"
    )
    assert isinstance(count, int)
    assert count > 0


def test_count_tokens_unknown_raises():
    from mesa_memory.adapter.tokenizer import count_tokens

    with pytest.raises(ValueError, match="Unknown adapter_type"):
        count_tokens("Hello", adapter_type="unknown")


def test_enforce_context_limit_passes():
    from mesa_memory.adapter.tokenizer import enforce_context_limit

    enforce_context_limit("Hello", adapter_type="claude", model_id="", limit=1000)


def test_enforce_context_limit_raises():
    from mesa_memory.adapter.base import TokenBudgetExceededError
    from mesa_memory.adapter.tokenizer import enforce_context_limit

    with pytest.raises(TokenBudgetExceededError):
        enforce_context_limit(
            "word " * 5000,
            adapter_type="claude",
            model_id="",
            limit=10,
        )


def test_adapter_factory_auto_detect_zero_cost(monkeypatch):
    from mesa_memory.adapter.factory import AdapterFactory, reset_legacy_warnings
    from mesa_memory.adapter.openai_compat import OpenAICompatibleAdapter
    from mesa_memory.config import config

    reset_legacy_warnings()
    monkeypatch.setattr(config, "llm_model", "ollama:qwen3:8b")
    monkeypatch.setattr(config, "llm_base_url", None)
    monkeypatch.setattr(config, "llm_api_key", None)
    monkeypatch.setattr(config, "external_provider_enabled", False)
    monkeypatch.setenv("MESA_OLLAMA_URL", "http://localhost:11434")
    adapter = AdapterFactory.get_adapter()
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.model_name == "qwen3:8b"
    assert adapter.base_url == "http://localhost:11434/v1"
    assert adapter.api_key == "local"
