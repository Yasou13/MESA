import os
from unittest.mock import MagicMock

import pytest

from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.valence.core import ValenceMotor


def _make_mock_adapter():
    adapter = MagicMock()
    adapter.EMBEDDING_DIM = 768
    adapter.complete.return_value = '{"decision": "DISCARD", "justification": "test"}'
    return adapter


@pytest.mark.asyncio
async def test_valence_persistence(tmp_path):
    """Test that ValenceMotor state (threshold, memory count) is properly saved and loaded."""
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()

    # 1. Initialize first motor and alter its state
    motor1 = ValenceMotor(llm_adapter=adapter, obs_layer=obs)
    motor1.memory_count = 42
    motor1._ewmad_threshold = 0.88

    # 2. Save state to a temporary SQLite database
    db_path = str(tmp_path / "valence_state.db")
    await motor1.save_state(db_path)

    assert os.path.exists(db_path)

    # 3. Initialize a fresh motor
    motor2 = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    # Pre-condition: motor2 should have default state
    assert motor2.memory_count == 0
    assert motor2._ewmad_threshold == motor2.bootstrap_threshold

    # 4. Load state
    await motor2.load_state(db_path)

    # 5. Assertions: state must match motor1
    assert motor2.memory_count == 42
    assert motor2._ewmad_threshold == 0.88


@pytest.mark.asyncio
async def test_valence_persistence_is_tenant_scoped_and_ignores_legacy_global_state(
    tmp_path,
):
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    db_path = str(tmp_path / "valence_state.db")

    motor1 = ValenceMotor(llm_adapter=adapter, obs_layer=obs)
    alpha = motor1._state_for("agent-alpha")
    alpha.memory_count = 12
    alpha.ewmad_threshold = 0.77
    beta = motor1._state_for("agent-beta")
    beta.memory_count = 4
    beta.ewmad_threshold = 0.66
    await motor1.save_state(db_path)

    motor2 = ValenceMotor(llm_adapter=adapter, obs_layer=obs)
    await motor2.load_state(db_path)

    restored_alpha = motor2._state_for("agent-alpha")
    restored_beta = motor2._state_for("agent-beta")
    assert (restored_alpha.memory_count, restored_alpha.ewmad_threshold) == (12, 0.77)
    assert (restored_beta.memory_count, restored_beta.ewmad_threshold) == (4, 0.66)
    assert motor2.memory_count == 0
