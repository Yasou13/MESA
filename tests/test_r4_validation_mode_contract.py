import pytest
from pydantic import ValidationError

from mesa_memory.config import MesaConfig


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
    invalid_values = [-1, 3, 10, -5, "auto", "two", "invalid", "-1", "3", "auto ", "none", "True", True]
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
