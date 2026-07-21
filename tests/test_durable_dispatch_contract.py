"""WAVE-004A SQLite durable dispatch intent/receipt contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class Vector:
    async def get_active_node_ids(self, agent_id=None):
        return set()


async def env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "dispatch.db"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sql, Vector()), sql


@pytest.mark.asyncio
async def test_raw_log_dispatch_is_idempotent_and_receipted(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        log_id = await dao.insert_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "dispatch"}
        )
        intents = await asyncio.gather(
            dao.dispatch_raw_log("tenant-a", log_id, worker_id="dispatcher-a"),
            dao.dispatch_raw_log("tenant-a", log_id, worker_id="dispatcher-b"),
        )
        assert len({item["dispatch_id"] for item in intents}) == 1
        assert intents[0]["state"] == "RECEIPT_RECORDED"
        receipt = await dao.get_dispatch_receipt(intents[0]["dispatch_id"])
        assert receipt["agent_id"] == "tenant-a"
        assert receipt["queue_record_id"] == intents[0]["queue_record_id"]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_recovery_enqueues_pending_raw_log_without_scope_reconstruction(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        log_id = await dao.insert_raw_log(
            "tenant-b", {"agent_id": "tenant-b", "content": "recover"}
        )
        recovered = await dao.recover_raw_log_dispatches(worker_id="restart-dispatcher")
        assert any(item["source_record_id"] == log_id for item in recovered)
        receipt = await dao.get_dispatch_receipt_by_source("tenant-b", log_id)
        assert receipt["agent_id"] == "tenant-b"
    finally:
        await sql.close()
