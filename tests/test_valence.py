import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.valence.core import ValenceMotor
from mesa_memory.valence.drift import recalibrate_threshold


def _make_mock_adapter():
    adapter = MagicMock()
    adapter.EMBEDDING_DIM = 768
    adapter.complete.return_value = json.dumps(
        {"decision": "DISCARD", "justification": "test"}
    )
    return adapter


def _make_cmb_candidate(embedding=None, latency_ms=100.0):
    return {
        "content_payload": "test content",
        "source": "agent",
        "performative": "assert",
        "resource_cost": {"token_count": 50, "latency_ms": latency_ms},
        "embedding": embedding or [0.1] * 768,
    }


@pytest.mark.asyncio
async def test_tier1_bypass():
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    motor = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    cmb = _make_cmb_candidate()
    signals = {"explicit_correction": True}

    result = await motor.evaluate(cmb, signals)

    assert result is True
    adapter.complete.assert_not_called()


@pytest.mark.asyncio
async def test_tier1_error_state():
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    motor = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    cmb = _make_cmb_candidate()
    signals = {"error": True}

    result = await motor.evaluate(cmb, signals)

    # Operational behavior: ExecutionFailure should discard the CMB (return False)
    # The previous faulty test logic expected True, which is incorrect.
    assert result is False
    adapter.complete.assert_not_called()


@pytest.mark.asyncio
async def test_tier2_ecod_bootstrap():
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    motor = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    rng = np.random.RandomState(42)
    for _ in range(10):
        motor.existing_embeddings.append(rng.rand(768).tolist())
        motor.memory_count += 1

    assert motor.memory_count < 50
    threshold = motor._get_current_threshold()
    assert threshold == motor.bootstrap_threshold

    novel_embedding = rng.rand(768).tolist()
    cmb = _make_cmb_candidate(embedding=novel_embedding)
    signals = {}

    await motor.evaluate(cmb, signals)

    tier2_logs = [
        c
        for c in obs.metrics.counters
        if "valence_tier_2" in c or "valence_decision" in c
    ]
    assert len(tier2_logs) > 0


def test_threshold_recalibration_ewmad():
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    motor = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    initial_threshold = motor.bootstrap_threshold
    assert initial_threshold == 0.75

    rng = np.random.RandomState(99)
    for i in range(160):
        emb = rng.rand(768).tolist()
        motor.existing_embeddings.append(emb)
        motor.memory_count += 1
        motor._records_since_recalibration += 1
        if motor._records_since_recalibration >= 50:
            motor._recalibrate()

    assert motor.memory_count == 160
    # Verify threshold has been modified by EWMAD recalibration
    assert (
        motor._ewmad_threshold != initial_threshold
    ), "Threshold should have drifted after 160 records"

    recalibrated = recalibrate_threshold(0.75, motor.existing_embeddings)
    assert 0.50 <= recalibrated <= 0.90


@pytest.mark.asyncio
async def test_tier3_deferred():
    adapter = _make_mock_adapter()
    obs = ObservabilityLayer()
    motor = ValenceMotor(llm_adapter=adapter, obs_layer=obs)

    motor.existing_embeddings = [np.ones(768).tolist()] * 5
    motor.memory_count = 5

    near_duplicate = np.ones(768).tolist()
    cmb = _make_cmb_candidate(embedding=near_duplicate, latency_ms=100.0)
    result = await motor.evaluate(cmb, {})

    # Tier-3 deferral now returns the status string "DEFERRED", not a boolean.
    assert result == "DEFERRED"
    assert cmb.get("tier3_deferred") is True
    adapter.complete.assert_not_called()
