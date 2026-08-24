"""Deterministic tests for VectorEngine's mutation-triggered Lance maintenance."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import mesa_storage.vector_engine as vector_engine_module
from mesa_storage.vector_engine import VectorEngine


@pytest.fixture
def engine() -> VectorEngine:
    engine = VectorEngine(
        "unused",
        maintenance_mutation_threshold=3,
        maintenance_min_interval_seconds=0,
        maintenance_failure_retry_seconds=0,
    )
    yield engine
    engine._executor.shutdown(wait=True, cancel_futures=True)


def _install_fake_executor(monkeypatch, *, pause: bool = False) -> dict[str, int]:
    """Keep maintenance tests deterministic and independent of native I/O."""
    state = {"in_progress": 0, "peak": 0}

    async def run_in_executor(_executor, function, *args):
        state["in_progress"] += 1
        state["peak"] = max(state["peak"], state["in_progress"])
        if pause:
            await asyncio.sleep(0.01)
        try:
            return function(*args)
        finally:
            state["in_progress"] -= 1

    loop = SimpleNamespace(run_in_executor=run_in_executor)
    monkeypatch.setattr(vector_engine_module.asyncio, "get_running_loop", lambda: loop)
    return state


def test_maintenance_configuration_rejects_invalid_values() -> None:
    """Maintenance limits must fail before an engine allocates resources."""
    for options in (
        {"maintenance_mutation_threshold": 0},
        {"maintenance_min_interval_seconds": -1},
        {"maintenance_failure_retry_seconds": -1},
    ):
        with pytest.raises(ValueError):
            VectorEngine("unused", **options)


def test_metrics_snapshot_includes_maintenance_average() -> None:
    engine = VectorEngine("unused")
    engine.metrics.searches = 2
    engine.metrics.total_search_time_ms = 5.0
    engine.metrics.maintenance_runs = 2
    engine.metrics.maintenance_failures = 1
    engine.metrics.total_maintenance_time_ms = 7.0

    assert engine.metrics.snapshot() == {
        "upserts": 0,
        "searches": 2,
        "soft_deletes": 0,
        "errors": 0,
        "avg_search_time_ms": 2.5,
        "maintenance_runs": 2,
        "maintenance_failures": 1,
        "avg_maintenance_time_ms": 3.5,
    }
    engine._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_public_writes_report_mutations_after_success(monkeypatch) -> None:
    engine = VectorEngine("unused")
    engine._initialized = True
    engine._sync_upsert = MagicMock()  # type: ignore[method-assign]
    engine._sync_bulk_upsert = MagicMock(return_value=3)  # type: ignore[method-assign]
    record_mutations = AsyncMock()
    engine._record_mutations_and_maybe_maintain = record_mutations  # type: ignore[method-assign]
    _install_fake_executor(monkeypatch)

    await engine.upsert("node-1", "agent-1", [1.0, 2.0])
    assert record_mutations.await_args_list[0].args == ({"mesa_vectors_2": 1},)

    result = await engine.bulk_upsert(
        [
            {"node_id": "node-1", "agent_id": "agent-1", "embedding": [1.0, 2.0]},
            {"node_id": "node-2", "agent_id": "agent-1", "embedding": [3.0, 4.0]},
            {"node_id": "node-3", "agent_id": "agent-1", "embedding": [5.0, 6.0, 7.0]},
        ]
    )

    assert result == 3
    assert record_mutations.await_args_list[1].args == (
        {"mesa_vectors_2": 2, "mesa_vectors_3": 1},
    )
    await engine.close()


@pytest.mark.asyncio
async def test_close_clears_pending_maintenance_state() -> None:
    engine = VectorEngine("unused")
    engine._initialized = True
    engine._mutations_since_maintenance["mesa_vectors_8"] = 3
    engine._maintenance_last_attempt["mesa_vectors_8"] = 1.0
    engine._maintenance_last_failure["mesa_vectors_8"] = 2.0

    await engine.close()

    assert not engine._initialized
    assert engine._db is None
    assert engine._mutations_since_maintenance == {}
    assert engine._maintenance_last_attempt == {}
    assert engine._maintenance_last_failure == {}


@pytest.mark.asyncio
async def test_maintenance_waits_for_mutation_threshold(
    engine: VectorEngine, monkeypatch
) -> None:
    optimize = MagicMock()
    engine._sync_optimize_table = optimize  # type: ignore[method-assign]

    for _ in range(2):
        await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})

    optimize.assert_not_called()
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 2

    _install_fake_executor(monkeypatch)
    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})

    optimize.assert_called_once_with("mesa_vectors_8")
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 0
    assert engine.metrics.maintenance_runs == 1


@pytest.mark.asyncio
async def test_maintenance_failure_keeps_pending_count_for_retry(
    engine: VectorEngine, monkeypatch
) -> None:
    optimize = MagicMock(side_effect=RuntimeError("Lance unavailable"))
    engine._sync_optimize_table = optimize  # type: ignore[method-assign]
    _install_fake_executor(monkeypatch)

    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 3})

    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 3
    assert engine.metrics.maintenance_runs == 0
    assert engine.metrics.maintenance_failures == 1

    optimize.side_effect = None
    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})

    assert optimize.call_count == 2
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 0
    assert engine.metrics.maintenance_runs == 1


@pytest.mark.asyncio
async def test_maintenance_failure_is_rate_limited_by_default(monkeypatch) -> None:
    engine = VectorEngine("unused", maintenance_mutation_threshold=1)
    optimize = MagicMock(side_effect=RuntimeError("Lance unavailable"))
    engine._sync_optimize_table = optimize  # type: ignore[method-assign]
    _install_fake_executor(monkeypatch)

    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})
    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})

    optimize.assert_called_once()
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 2
    engine._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_minimum_interval_coalesces_due_maintenance(
    engine: VectorEngine, monkeypatch
) -> None:
    engine._maintenance_min_interval_seconds = 60
    optimize = MagicMock()
    engine._sync_optimize_table = optimize  # type: ignore[method-assign]
    _install_fake_executor(monkeypatch)

    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 3})
    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 3})

    optimize.assert_called_once()
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 3

    engine._maintenance_last_attempt["mesa_vectors_8"] = time.monotonic() - 61
    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})

    assert optimize.call_count == 2
    assert engine._mutations_since_maintenance["mesa_vectors_8"] == 0


@pytest.mark.asyncio
async def test_concurrent_due_calls_never_overlap_optimization(
    engine: VectorEngine, monkeypatch
) -> None:
    engine._maintenance_mutation_threshold = 1
    engine._sync_optimize_table = MagicMock()  # type: ignore[method-assign]
    executor_state = _install_fake_executor(monkeypatch, pause=True)

    await asyncio.gather(
        *(
            engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 1})
            for _ in range(8)
        )
    )

    assert executor_state["peak"] == 1
    assert engine.metrics.maintenance_runs == 8


def test_optimize_uses_safe_retention_configuration() -> None:
    cleanup_age = timedelta(days=2)
    engine = VectorEngine("unused", maintenance_cleanup_older_than=cleanup_age)
    table = MagicMock()
    db = MagicMock()
    db.open_table.return_value = table
    engine._db = db

    engine._sync_optimize_table("mesa_vectors_8")

    table.optimize.assert_called_once_with(
        cleanup_older_than=cleanup_age,
        delete_unverified=False,
    )
    engine._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_disabled_maintenance_does_not_schedule_work(monkeypatch) -> None:
    engine = VectorEngine("unused", maintenance_enabled=False)
    optimize = MagicMock()
    engine._sync_optimize_table = optimize  # type: ignore[method-assign]
    _install_fake_executor(monkeypatch)

    await engine._record_mutations_and_maybe_maintain({"mesa_vectors_8": 100})

    optimize.assert_not_called()
    engine._executor.shutdown(wait=True, cancel_futures=True)
