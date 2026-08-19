"""Adversarial V3 startup orphan-reconciliation boundary tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _dao(tmp_path, vector) -> tuple[MemoryDAO, AsyncEngine]:  # type: ignore[no-untyped-def]
    engine = AsyncEngine(str(tmp_path / "legacy-orphans.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    return MemoryDAO(sqlite_engine=engine, vector_engine=vector), engine


async def _insert_nodes(engine: AsyncEngine, rows: list[tuple[str, str, str]]) -> None:
    async with engine.transaction() as db:
        await db.executemany(
            "INSERT INTO nodes "
            "(id, entity_name, type, content_payload, is_consolidated, "
            "created_at, agent_id, session_id) "
            "VALUES (?, ?, 'ENTITY', 'legacy content', 0, ?, ?, 'session-a')",
            [
                (node_id, node_id, created_at, agent_id)
                for node_id, agent_id, created_at in rows
            ],
        )
        await db.commit()


async def _invalid_at(engine: AsyncEngine, node_id: str) -> str | None:
    async with engine.connection() as db:
        async with db.execute(
            "SELECT invalid_at FROM nodes WHERE id = ?", (node_id,)
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_reconciliation_reaches_orphan_after_first_100_agents(tmp_path) -> None:
    orphan_id = "node-agent-100"

    async def existing_for_agent(agent_id: str, node_ids: list[str]) -> set[str]:
        assert all(node_id == f"node-{agent_id}" for node_id in node_ids)
        return {node_id for node_id in node_ids if node_id != orphan_id}

    vector = MagicMock()
    vector.get_existing_node_ids = AsyncMock(side_effect=existing_for_agent)
    dao, engine = await _dao(tmp_path, vector)
    created_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (f"node-agent-{index:03d}", f"agent-{index:03d}", created_at)
        for index in range(101)
    ]
    try:
        await _insert_nodes(engine, rows)
        await dao._reconcile_orphaned_nodes()

        assert await _invalid_at(engine, orphan_id) is not None
        assert vector.get_existing_node_ids.await_count == 101
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reconciliation_reaches_orphan_after_first_500_records(tmp_path) -> None:
    orphan_id = "node-000"

    async def existing_for_agent(agent_id: str, node_ids: list[str]) -> set[str]:
        assert agent_id == "agent-a"
        return {node_id for node_id in node_ids if node_id != orphan_id}

    vector = MagicMock()
    vector.get_existing_node_ids = AsyncMock(side_effect=existing_for_agent)
    dao, engine = await _dao(tmp_path, vector)
    base = datetime.now(timezone.utc)
    rows = [
        (
            f"node-{index:03d}",
            "agent-a",
            (base + timedelta(seconds=index)).isoformat(),
        )
        for index in range(501)
    ]
    try:
        await _insert_nodes(engine, rows)
        await dao._reconcile_orphaned_nodes()

        assert await _invalid_at(engine, orphan_id) is not None
        assert vector.get_existing_node_ids.await_count == 2
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_vector_unavailable_remains_startup_fail_open_and_retriable(
    tmp_path,
) -> None:
    vector = MagicMock()
    vector.get_existing_node_ids = AsyncMock(
        side_effect=RuntimeError("vector temporarily unavailable")
    )
    dao, engine = await _dao(tmp_path, vector)
    try:
        await _insert_nodes(
            engine,
            [("possible-orphan", "agent-a", datetime.now(timezone.utc).isoformat())],
        )

        await dao._reconcile_orphaned_nodes()

        assert await _invalid_at(engine, "possible-orphan") is None
        vector.get_existing_node_ids.assert_awaited_once_with(
            "agent-a", ["possible-orphan"]
        )
    finally:
        await engine.close()
