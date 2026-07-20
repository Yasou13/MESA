from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@dataclass
class FakeGraph:
    active_node_ids: set[tuple[str, str]] = field(default_factory=set)
    delete_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    fail_delete: bool = False

    async def delete_nodes(
        self, *, purge_id: str, agent_id: str, node_ids: list[str]
    ) -> None:
        self.delete_calls.append((purge_id, tuple(node_ids)))
        if self.fail_delete:
            raise RuntimeError("simulated Kuzu delete failure")
        for node_id in node_ids:
            self.active_node_ids.discard((agent_id, node_id))

    async def verify_nodes_absent(
        self, *, agent_id: str, node_ids: list[str]
    ) -> bool:
        return all((agent_id, node_id) not in self.active_node_ids for node_id in node_ids)


@dataclass
class FakeVector:
    active_node_ids: set[tuple[str, str]] = field(default_factory=set)
    hard_delete_calls: list[tuple[str, str]] = field(default_factory=list)
    fail_hard_delete: bool = False

    async def soft_delete(self, node_id: str, agent_id: str) -> None:
        self.active_node_ids.discard((agent_id, node_id))

    async def hard_delete(self, node_id: str, agent_id: str) -> None:
        self.hard_delete_calls.append((agent_id, node_id))
        if self.fail_hard_delete:
            raise RuntimeError("simulated vector delete failure")
        self.active_node_ids.discard((agent_id, node_id))

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        return {
            node_id
            for stored_agent_id, node_id in self.active_node_ids
            if agent_id is None or stored_agent_id == agent_id
        }

    async def search(self, *, query_vector, limit, agent_id):
        return [
            {
                "node_id": node_id,
                "agent_id": stored_agent_id,
                "_distance": 0.0,
            }
            for stored_agent_id, node_id in self.active_node_ids
            if stored_agent_id == agent_id
        ][:limit]


async def _make_env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "purge-journal.db"), max_connections=2)
    await sql.initialize()
    await initialize_schema(sql)
    # Alembic head is safe to apply again on the same synthetic database.
    await initialize_schema(sql)
    graph = FakeGraph()
    vector = FakeVector()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)

    rows = [
        ("a-session-1", "Agent A session", "agent-a", "session-a"),
        ("a-session-2", "Agent A other", "agent-a", "session-b"),
        ("b-session-1", "Agent B", "agent-b", "session-a"),
    ]
    async with sql.transaction() as db:
        await db.executemany(
            "INSERT INTO nodes "
            "(id, entity_name, type, content_payload, is_consolidated, created_at, agent_id, session_id) "
            "VALUES (?, ?, 'ENTITY', '', 0, CURRENT_TIMESTAMP, ?, ?)",
            rows,
        )
        await db.commit()

    for node_id, _name, agent_id, _session_id in rows:
        graph.active_node_ids.add((agent_id, node_id))
        vector.active_node_ids.add((agent_id, node_id))

    return dao, sql, graph, vector


async def _journal(sql, idempotency_key: str):
    async with sql.connection() as db:
        async with db.execute(
            "SELECT purge_id, state, agent_id, scope, session_id, retry_count "
            "FROM purge_journal WHERE idempotency_key = ?",
            (idempotency_key,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


@pytest.mark.asyncio
async def test_successful_exact_scope_purge_finalizes_and_hides_tombstone(tmp_path):
    dao, sql, graph, vector = await _make_env(tmp_path)
    try:
        deleted = await dao.purge_memory(
            "agent-a",
            scope="session",
            session_id="session-a",
            principal_id="principal-a",
            idempotency_key="purge-success",
        )

        assert deleted == 1
        journal = await _journal(sql, "purge-success")
        assert journal is not None
        assert journal["state"] == "FINALIZED"
        assert graph.delete_calls == [(journal["purge_id"], ("a-session-1",))]
        assert vector.hard_delete_calls == [("agent-a", "a-session-1")]
        with pytest.raises(RuntimeError, match="FINALIZED"):
            await dao.purge_memory(
                "agent-a",
                scope="session",
                session_id="session-a",
                principal_id="principal-a",
                idempotency_key="purge-success",
            )
        assert {row["id"] for row in await dao.get_memories("agent-a")} == {"a-session-2"}
        assert {
            result["node_id"]
            for result in await dao.search_memory(
                "agent-a", query_vector=VEC8, include_graph=False
            )
        } == {"a-session-2"}
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_kuzu_failure_keeps_tombstone_and_never_starts_vector_delete(tmp_path):
    dao, sql, graph, vector = await _make_env(tmp_path)
    graph.fail_delete = True
    try:
        with pytest.raises(RuntimeError, match="Kuzu"):
            await dao.purge_memory(
                "agent-a",
                scope="session",
                session_id="session-a",
                principal_id="principal-a",
                idempotency_key="purge-kuzu-failure",
            )

        journal = await _journal(sql, "purge-kuzu-failure")
        assert journal is not None
        assert journal["state"] == "RETRY_PENDING"
        assert vector.hard_delete_calls == []
        assert all(row["id"] != "a-session-1" for row in await dao.get_memories("agent-a"))
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_vector_failure_retries_only_missing_step_from_same_purge_id(tmp_path):
    dao, sql, graph, vector = await _make_env(tmp_path)
    vector.fail_hard_delete = True
    try:
        with pytest.raises(RuntimeError, match="vector"):
            await dao.purge_memory(
                "agent-a",
                scope="session",
                session_id="session-a",
                principal_id="principal-a",
                idempotency_key="purge-vector-failure",
            )

        journal = await _journal(sql, "purge-vector-failure")
        assert journal is not None
        assert journal["state"] == "RETRY_PENDING"
        assert len(graph.delete_calls) == 1

        vector.fail_hard_delete = False
        recovery = await dao.resume_incomplete_purges()
        assert recovery == {journal["purge_id"]: "FINALIZED"}
        assert (await _journal(sql, "purge-vector-failure"))["state"] == "FINALIZED"
        assert len(graph.delete_calls) == 1
        assert vector.hard_delete_calls == [
            ("agent-a", "a-session-1"),
            ("agent-a", "a-session-1"),
        ]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_exact_scope_rejects_wildcard_and_does_not_touch_other_tenant_or_session(tmp_path):
    dao, sql, _graph, _vector = await _make_env(tmp_path)
    try:
        with pytest.raises(ValueError):
            await dao.purge_memory("agent-a", scope="*")
        with pytest.raises(ValueError):
            await dao.purge_memory("agent-a", scope="session", session_id="*")

        await dao.purge_memory(
            "agent-a",
            scope="session",
            session_id="session-a",
            principal_id="principal-a",
            idempotency_key="purge-exact-scope",
        )
        agent_a = await dao.get_memories("agent-a")
        agent_b = await dao.get_memories("agent-b")
        assert {row["id"] for row in agent_a} == {"a-session-2"}
        assert {row["id"] for row in agent_b} == {"b-session-1"}
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_pre_downstream_rollback_is_scope_bound_but_finalized_purge_cannot_restore(tmp_path):
    dao, sql, graph, _vector = await _make_env(tmp_path)
    graph.fail_delete = True
    try:
        with pytest.raises(RuntimeError):
            await dao.purge_memory(
                "agent-a",
                scope="session",
                session_id="session-a",
                principal_id="principal-a",
                idempotency_key="purge-rollback",
            )
        journal = await _journal(sql, "purge-rollback")
        assert journal is not None
        restored = await dao.rollback_purge(journal["purge_id"])
        assert restored == 1
        assert {row["id"] for row in await dao.get_memories("agent-a")} == {
            "a-session-1",
            "a-session-2",
        }
        assert ("agent-a", "a-session-1") in graph.active_node_ids
        assert ("agent-a", "a-session-1") in _vector.active_node_ids

        graph.fail_delete = False
        await dao.purge_memory(
            "agent-a",
            scope="session",
            session_id="session-a",
            principal_id="principal-a",
            idempotency_key="purge-finalized",
        )
        finalized = await _journal(sql, "purge-finalized")
        with pytest.raises(RuntimeError):
            await dao.rollback_purge(finalized["purge_id"])
    finally:
        await sql.close()

@pytest.mark.asyncio
async def test_router_rejects_cross_tenant_purge_without_principal_purge_grant():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from mesa_api.router import create_memory_router
    from mesa_api.schemas import MemoryPurgeRequest

    class AccessControlStub:
        async def check_principal_permission(self, principal_id, agent_id, permission):
            assert principal_id == "principal-a"
            assert agent_id == "agent-b"
            assert permission == "PURGE"
            return False

        async def check_access(self, *_args):
            return True

    dao = SimpleNamespace(purge_memory=AsyncMock(return_value=1))
    router = create_memory_router(
        get_dao=lambda: dao,
        get_access_control=lambda: AccessControlStub(),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/v3/memory/purge")
    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=SimpleNamespace(principal_id="principal-a", status="active")
        )
    )
    payload = MemoryPurgeRequest(
        agent_id="agent-b", scope="agent", scope_id="agent-b"
    )

    with pytest.raises(HTTPException) as raised:
        await endpoint(request=request, payload=payload, dao=dao)

    assert raised.value.status_code == 403
    dao.purge_memory.assert_not_awaited()

@pytest.mark.asyncio
async def test_router_returns_retry_status_instead_of_partial_purge_success():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from mesa_api.router import create_memory_router
    from mesa_api.schemas import MemoryPurgeRequest
    from mesa_storage.dao import PurgeRetryPendingError

    class AccessControlStub:
        async def check_principal_permission(self, *_args):
            return True

        async def check_access(self, *_args):
            return True

    dao = SimpleNamespace(
        purge_memory=AsyncMock(side_effect=PurgeRetryPendingError("retry pending"))
    )
    router = create_memory_router(
        get_dao=lambda: dao,
        get_access_control=lambda: AccessControlStub(),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/v3/memory/purge")
    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=SimpleNamespace(principal_id="principal-a", status="active")
        )
    )
    payload = MemoryPurgeRequest(
        agent_id="agent-a", scope="agent", scope_id="agent-a"
    )

    with pytest.raises(HTTPException) as raised:
        await endpoint(request=request, payload=payload, dao=dao)

    assert raised.value.status_code == 503
