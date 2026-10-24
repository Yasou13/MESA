"""Worker job correlation and context-cleanup contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import structlog

import mesa_workers.ingestion_worker as worker
from tests.v4_release_helpers import insert_memory
from tests.v4_release_helpers import v4_release_env as v4_release_env


@pytest.mark.asyncio
async def test_cold_path_binds_operation_context_and_always_clears(monkeypatch):
    observed: dict = {}

    async def implementation(*args, **kwargs):
        observed.update(structlog.contextvars.get_contextvars())

    monkeypatch.setattr(worker, "_process_cold_path_impl", implementation)
    await worker.process_cold_path(42, "agent-a", AsyncMock())

    assert observed["operation_id"] == "42"
    assert observed["agent_id"] == "agent-a"
    assert observed["worker_id"] == "cold-path"
    assert "claim_token" not in observed
    assert structlog.contextvars.get_contextvars() == {}


@pytest.mark.asyncio
async def test_cold_path_does_not_bind_unvalidated_agent_id(monkeypatch):
    observed: dict = {}

    async def implementation(*args, **kwargs):
        observed.update(structlog.contextvars.get_contextvars())

    monkeypatch.setattr(worker, "_process_cold_path_impl", implementation)
    await worker.process_cold_path(42, "agent with spaces", AsyncMock())

    assert observed["agent_id"] == "invalid"
    assert "agent with spaces" not in observed.values()


def test_worker_source_does_not_emit_raw_payload_or_daemon_prints():
    ingestion_source = (
        worker.__file__ and open(worker.__file__, encoding="utf-8").read()
    )
    assert "RAW_LOG {raw_log}" not in ingestion_source
    assert "raw=%s" not in ingestion_source



@pytest.mark.asyncio
async def test_v4_insert_raw_log_id_is_the_worker_operation_id(
    v4_release_env, monkeypatch
):
    """The raw_log_id a V4 client receives is the worker log operation_id."""
    from mesa_memory.api.server import _consume_combined_durable_work_once

    admitted = await insert_memory(v4_release_env)
    observed: dict = {}

    async def implementation(*args, **kwargs):
        observed.update(structlog.contextvars.get_contextvars())

    monkeypatch.setattr(worker, "_process_cold_path_impl", implementation)
    await _consume_combined_durable_work_once(
        v4_release_env.dao, consolidation_loop=None, model_processing_enabled=False
    )

    assert observed["operation_id"] == str(admitted["raw_log_id"])
    assert observed["worker_id"] == "cold-path"
