"""Worker job correlation and context-cleanup contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import structlog

import mesa_workers.ingestion_worker as worker


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

    from mesa_memory import worker_runtime

    runtime_source = open(worker_runtime.__file__, encoding="utf-8").read()
    assert 'print("WORKER_RUNTIME=' not in runtime_source


def test_api_enqueue_and_worker_share_log_id_correlation_contract():
    from mesa_api import router

    api_source = open(router.__file__, encoding="utf-8").read()
    assert '"memory_insert_queued"' in api_source
    assert "bind_contextvars(operation_id=str(log_id))" in api_source
