from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import MesaConfig, config
from mesa_memory.consolidation.policy import compose_validation_policy


def test_explicit_tier3_modes_accepted():
    for mode in (0, 1, 2):
        cfg = MesaConfig(tier3_mode=mode)
        assert cfg.tier3_mode == mode
        assert cfg.effective_tier3_mode(model_enabled=True) == mode
        assert cfg.effective_tier3_mode(model_enabled=False) == mode


def test_string_tier3_modes_parsed_and_accepted():
    for mode_str, expected in [("0", 0), ("1", 1), ("2", 2), (" 0 ", 0), (" 2\n", 2)]:
        cfg = MesaConfig(MESA_TIER3_MODE=mode_str)
        assert cfg.tier3_mode == expected
        assert cfg.effective_tier3_mode() == expected


def test_invalid_explicit_modes_rejected():
    invalid_values = [
        -1,
        3,
        10,
        -5,
        "auto",
        "two",
        "invalid",
        "-1",
        "3",
        "auto ",
        "none",
        "True",
        True,
    ]
    for inv in invalid_values:
        with pytest.raises(ValidationError):
            MesaConfig(tier3_mode=inv)


def test_unset_compatibility_default():
    # When unset (None), effective mode depends on model_enabled
    cfg_unset = MesaConfig(tier3_mode=None)
    assert cfg_unset.tier3_mode is None
    # model_enabled=True -> preserves dual-LLM validation (Mode 2)
    assert cfg_unset.effective_tier3_mode(model_enabled=True) == 2
    # model_enabled=False -> no validation composed (Mode 0)
    assert cfg_unset.effective_tier3_mode(model_enabled=False) == 0


def test_empty_string_treated_as_unset():
    cfg = MesaConfig(MESA_TIER3_MODE="")
    assert cfg.tier3_mode is None
    assert cfg.effective_tier3_mode(model_enabled=True) == 2
    assert cfg.effective_tier3_mode(model_enabled=False) == 0


def test_zero_cost_mode_preserves_explicit_validation_assurance(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MESA_OLLAMA_URL", "http://test-ollama:11434")

    cfg = MesaConfig(tier3_mode=2, zero_cost_mode=True)

    assert cfg.effective_tier3_mode(model_enabled=True) == 2


def test_runtime_composition_constructs_exact_selected_validator_count(monkeypatch):
    created: list[tuple[str | None, str | None]] = []

    def provider_boundary(provider=None, *, model_name=None):
        created.append((provider, model_name))
        return MagicMock(model_name=model_name)

    monkeypatch.setattr(AdapterFactory, "get_adapter", staticmethod(provider_boundary))
    monkeypatch.setattr(config, "tier3_llm_provider_a", "mock")
    monkeypatch.setattr(config, "tier3_llm_model_name_a", "validator-a")
    monkeypatch.setattr(config, "tier3_llm_provider_b", "mock")
    monkeypatch.setattr(config, "tier3_llm_model_name_b", "validator-b")

    assert compose_validation_policy(0).validator_count == 0
    assert created == []
    assert compose_validation_policy(1).validator_count == 1
    assert created == [("mock", "validator-a")]
    assert compose_validation_policy(2).validator_count == 2
    assert created == [
        ("mock", "validator-a"),
        ("mock", "validator-a"),
        ("mock", "validator-b"),
    ]


@pytest.mark.parametrize(
    ("provider_a", "model_a", "provider_b", "model_b"),
    [
        (None, None, None, None),
        ("mock", "validator-a", None, None),
        ("mock", "same", "mock", "same"),
    ],
)
def test_mode_two_invalid_runtime_composition_fails_closed(
    monkeypatch, provider_a, model_a, provider_b, model_b
):
    monkeypatch.setattr(config, "tier3_llm_provider_a", provider_a)
    monkeypatch.setattr(config, "tier3_llm_model_name_a", model_a)
    monkeypatch.setattr(config, "tier3_llm_provider_b", provider_b)
    monkeypatch.setattr(config, "tier3_llm_model_name_b", model_b)

    with pytest.raises(ValueError):
        compose_validation_policy(2)


def test_mode_one_missing_a_fails_closed_without_constructing_b(monkeypatch):
    get_adapter = MagicMock()
    monkeypatch.setattr(AdapterFactory, "get_adapter", get_adapter)
    monkeypatch.setattr(config, "tier3_llm_provider_a", None)
    monkeypatch.setattr(config, "tier3_llm_model_name_a", None)
    monkeypatch.setattr(config, "tier3_llm_provider_b", "mock")
    monkeypatch.setattr(config, "tier3_llm_model_name_b", "validator-b")

    with pytest.raises(ValueError):
        compose_validation_policy(1)
    get_adapter.assert_not_called()


def test_claude_validator_factory_preserves_configured_model_identity(monkeypatch):
    from mesa_memory.adapter import claude

    class FakeClaudeAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(claude, "ClaudeAdapter", FakeClaudeAdapter)
    adapter = AdapterFactory.get_adapter("claude", model_name="claude-validator-b")

    assert adapter.kwargs["model_name"] == "claude-validator-b"
