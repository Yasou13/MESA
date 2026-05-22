from unittest.mock import patch

import numpy as np
import pytest

from mesa_memory.valence.novelty import _normalize_ecod_score, calculate_novelty_score


def test_normalize_ecod_score():
    assert _normalize_ecod_score(5.0, np.array([5.0, 5.0])) == 0.5
    assert _normalize_ecod_score(7.0, np.array([0.0, 10.0])) == 0.7
    assert _normalize_ecod_score(-1.0, np.array([0.0, 10.0])) == 0.0
    assert _normalize_ecod_score(11.0, np.array([0.0, 10.0])) == 1.0


@pytest.mark.asyncio
async def test_calculate_novelty_cold_start():
    res = await calculate_novelty_score([0.1], [], 0.5)
    assert res is True


@pytest.mark.asyncio
@patch("mesa_memory.valence.novelty.config")
async def test_calculate_novelty_fast_path(mock_config):
    mock_config.bootstrap_cosine_threshold = 0.9
    res = await calculate_novelty_score([1.0], [[-1.0]], 0.95)
    assert res is True


@pytest.mark.asyncio
@patch("mesa_memory.valence.novelty.config")
async def test_calculate_novelty_bootstrap(mock_config):
    mock_config.bootstrap_cosine_threshold = 0.1
    mock_config.recalibration_interval = 10

    # max_sim < cosine_threshold -> novel
    res = await calculate_novelty_score([1.0], [[1.0]], 1.5)
    assert res is True

    # max_sim >= cosine_threshold -> not novel
    res2 = await calculate_novelty_score([1.0], [[1.0]], 0.5)
    assert res2 is False


@pytest.mark.asyncio
@patch("mesa_memory.valence.novelty.config")
async def test_calculate_novelty_steady_state(mock_config):
    mock_config.bootstrap_cosine_threshold = 0.1
    mock_config.recalibration_interval = 2
    mock_config.ecod_anomaly_threshold = 0.8

    # Provide more points so ECOD assigns distinct train anomaly scores
    train_set = [[0.0, 0.0], [0.1, 0.1], [0.5, 0.5], [-0.1, -0.1], [0.9, 0.9]]
    res = await calculate_novelty_score([100.0, 100.0], train_set, 0.5)
    # ECOD will flag [100, 100] as highly anomalous (novel)
    assert res is True


# ===================================================================
# Missing Coverage Tests - Drift
# ===================================================================


def test_below_interval_returns_current():
    from mesa_memory.valence.drift import recalibrate_threshold

    # Fewer embeddings than recalibration_interval → unchanged
    embeddings = [[0.1] * 8 for _ in range(5)]
    result = recalibrate_threshold(0.75, embeddings)
    assert result == 0.75


def test_sufficient_data_recalibrates():
    from mesa_memory.config import config
    from mesa_memory.valence.drift import recalibrate_threshold

    np.random.seed(42)
    n = config.recalibration_interval * 2
    embeddings = [np.random.rand(8).tolist() for _ in range(n)]
    result = recalibrate_threshold(0.75, embeddings)
    # Must be within configured clamp range
    assert config.drift_clamp_min <= result <= config.drift_clamp_max


def test_no_historical_data_returns_current():
    from mesa_memory.config import config
    from mesa_memory.valence.drift import recalibrate_threshold

    # Exactly recalibration_interval → no historical data
    embeddings = [[0.1] * 8 for _ in range(config.recalibration_interval)]
    result = recalibrate_threshold(0.75, embeddings)
    assert result == 0.75
