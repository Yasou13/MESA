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
