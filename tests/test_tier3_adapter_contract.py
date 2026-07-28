"""Tier-3 must fail closed unless its validators are independently configured."""

import pytest

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import config


def test_tier3_rejects_matching_provider_and_model(monkeypatch) -> None:
    monkeypatch.setattr(config, "tier3_llm_provider_a", "ollama")
    monkeypatch.setattr(config, "tier3_llm_model_name_a", "model-a")
    monkeypatch.setattr(config, "tier3_llm_provider_b", "ollama")
    monkeypatch.setattr(config, "tier3_llm_model_name_b", "model-a")

    with pytest.raises(ValueError, match="different provider/model"):
        AdapterFactory.get_tier3_adapters()


def test_tier3_builds_two_explicit_adapter_specs(monkeypatch) -> None:
    monkeypatch.setattr(config, "tier3_llm_provider_a", "ollama")
    monkeypatch.setattr(config, "tier3_llm_model_name_a", "model-a")
    monkeypatch.setattr(config, "tier3_llm_provider_b", "openai_compatible")
    monkeypatch.setattr(config, "tier3_llm_model_name_b", "model-b")
    calls: list[tuple[str, str]] = []

    def adapter(provider: str, *, model_name: str):
        calls.append((provider, model_name))
        return (provider, model_name)

    monkeypatch.setattr(AdapterFactory, "get_adapter", adapter)

    assert AdapterFactory.get_tier3_adapters() == (
        ("ollama", "model-a"),
        ("openai_compatible", "model-b"),
    )
    assert calls == [("ollama", "model-a"), ("openai_compatible", "model-b")]
