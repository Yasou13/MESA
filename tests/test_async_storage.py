# MESA v0.3.0 — Phase 3: Async Storage & Graph Layer Test Suite
"""
Comprehensive tests for AsyncEngine, graph schema DDL, CRUD operations,
FTS5 lexical pre-filtering, and k-hop graph traversal.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.schemas import (
    bulk_insert_nodes,
    find_nodes_by_name,
    fts5_rebuild,
    fts5_search,
    get_active_edges,
    get_active_nodes,
    get_neighbors,
    initialize_schema,
    insert_edge,
    insert_node,
    k_hop_neighbors,
    mark_consolidated,
    soft_delete_edge,
    soft_delete_node,
    upsert_edge,
    validate_schema,
)
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "async_graph",
)

# Default agent_id used by insert_node/insert_edge when no explicit agent_id
# is provided.  All RLS-enforcing query functions now require this parameter.
_DEFAULT_AGENT = "__unset__"


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def engine():
    db_path = os.path.join(TEST_DIR, f"test_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()
    await initialize_schema(eng)
    yield eng
    await eng.close()


# ===================================================================
# AsyncEngine — lifecycle & PRAGMA enforcement
# ===================================================================


class TestAsyncEngine:
    @pytest.mark.asyncio
    async def test_initialize_creates_db_file(self):
        db_path = os.path.join(TEST_DIR, "init_test.db")
        eng = AsyncEngine(db_path)
        await eng.initialize()
        assert os.path.exists(db_path)
        await eng.close()

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self):
        db_path = os.path.join(TEST_DIR, "idempotent.db")
        eng = AsyncEngine(db_path)
        await eng.initialize()
        await eng.initialize()  # No-op second call
        assert eng.is_initialized
        await eng.close()

    @pytest.mark.asyncio
    async def test_connection_before_init_raises(self):
        eng = AsyncEngine(os.path.join(TEST_DIR, "noinit.db"))
        with pytest.raises(RuntimeError, match="has not been initialized"):
            async with eng.connection():
                pass

    @pytest.mark.asyncio
    async def test_pragma_wal_enforced(self):
        db_path = os.path.join(TEST_DIR, "pragma.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA journal_mode;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == "wal"

    @pytest.mark.asyncio
    async def test_pragma_synchronous_normal(self):
        db_path = os.path.join(TEST_DIR, "sync.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA synchronous;") as cur:
                    row = await cur.fetchone()
                    # 1 = NORMAL
                    assert row[0] == 1

    @pytest.mark.asyncio
    async def test_pragma_cache_size(self):
        db_path = os.path.join(TEST_DIR, "cache.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA cache_size;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == -64000

    @pytest.mark.asyncio
    async def test_pragma_foreign_keys(self):
        db_path = os.path.join(TEST_DIR, "fk.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA foreign_keys;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == 1

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        db_path = os.path.join(TEST_DIR, "ctx.db")
        async with AsyncEngine(db_path) as eng:
            assert eng.is_initialized
        assert not eng.is_initialized

    @pytest.mark.asyncio
    async def test_connection_metrics(self):
        db_path = os.path.join(TEST_DIR, "metrics.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                await db.execute("SELECT 1;")
            snap = eng.metrics.snapshot()
            assert snap["connections_opened"] >= 1
            assert snap["connections_closed"] >= 1

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        db_path = os.path.join(TEST_DIR, "health.db")
        async with AsyncEngine(db_path) as eng:
            result = await eng.health_check()
            assert result["status"] == "healthy"
            assert result["journal_mode"] == "wal"
            assert result["integrity"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self):
        eng = AsyncEngine(os.path.join(TEST_DIR, "noinit_health.db"))
        result = await eng.health_check()
        assert result["status"] == "not_initialized"

    @pytest.mark.asyncio
    async def test_checkpoint(self):
        db_path = os.path.join(TEST_DIR, "ckpt.db")
        async with AsyncEngine(db_path) as eng:
            result = await eng.checkpoint("PASSIVE")
            assert "busy" in result
            assert "log_pages" in result

    @pytest.mark.asyncio
    async def test_checkpoint_invalid_mode(self):
        db_path = os.path.join(TEST_DIR, "ckpt_bad.db")
        async with AsyncEngine(db_path) as eng:
            with pytest.raises(ValueError, match="Invalid checkpoint mode"):
                await eng.checkpoint("INVALID")

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_error(self):
        db_path = os.path.join(TEST_DIR, "txn.db")
        async with AsyncEngine(db_path) as eng:
            await initialize_schema(eng)
            nid = uuid.uuid4().hex
            await insert_node(eng, nid, "RollbackTest")

            with pytest.raises(ValueError):
                async with eng.transaction() as db:
                    await db.execute(
                        "UPDATE nodes SET entity_name = ? WHERE id = ?",
                        ("CHANGED", nid),
                    )
                    raise ValueError("force rollback")

            # Verify rollback
            nodes = await get_active_nodes(eng, agent_id=_DEFAULT_AGENT)
            found = [n for n in nodes if n["id"] == nid]
            assert found[0]["entity_name"] == "RollbackTest"

    @pytest.mark.asyncio
    async def test_concurrent_connections(self):
        db_path = os.path.join(TEST_DIR, "concurrent.db")
        async with AsyncEngine(db_path, max_connections=4) as eng:
            await initialize_schema(eng)

            async def _worker(idx: int) -> str:
                nid = uuid.uuid4().hex
                await insert_node(eng, nid, f"concurrent_{idx}")
                return nid

            ids = await asyncio.gather(*[_worker(i) for i in range(8)])
            assert len(ids) == 8
            nodes = await get_active_nodes(eng, agent_id=_DEFAULT_AGENT)
            assert len(nodes) == 8


# ===================================================================
# Schema validation
# ===================================================================


class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_schema_initializes_idempotently(self, engine):
        await initialize_schema(engine)  # Second call, no errors
        result = await validate_schema(engine)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_schema_all_objects(self, engine):
        result = await validate_schema(engine)
        assert result["valid"] is True
        assert all(result["tables"].values())
        assert all(result["indexes"].values())
        assert all(result["triggers"].values())


# ===================================================================
# Node CRUD
# ===================================================================


class TestNodeCRUD:
    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self, engine):
        nid = uuid.uuid4().hex
        returned = await insert_node(engine, nid, "TestEntity", "PERSON")
        assert returned == nid

        nodes = await get_active_nodes(engine, agent_id=_DEFAULT_AGENT)
        assert any(n["id"] == nid and n["entity_name"] == "TestEntity" for n in nodes)

    @pytest.mark.asyncio
    async def test_soft_delete_node(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "DeleteMe")
        await soft_delete_node(engine, nid, agent_id=_DEFAULT_AGENT)

        nodes = await get_active_nodes(engine, agent_id=_DEFAULT_AGENT)
        assert not any(n["id"] == nid for n in nodes)

    @pytest.mark.asyncio
    async def test_soft_delete_cascades_to_edges(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "Source")
        await insert_node(engine, n2, "Target")
        await insert_edge(engine, uuid.uuid4().hex, n1, n2, "RELATES_TO")

        await soft_delete_node(engine, n1, agent_id=_DEFAULT_AGENT)
        edges = await get_active_edges(engine, agent_id=_DEFAULT_AGENT)
        assert len(edges) == 0

    @pytest.mark.asyncio
    async def test_mark_consolidated(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "ConsolidateMe")
        await mark_consolidated(engine, nid, agent_id=_DEFAULT_AGENT)

        nodes = await get_active_nodes(engine, agent_id=_DEFAULT_AGENT)
        found = [n for n in nodes if n["id"] == nid]
        assert found[0]["is_consolidated"] == 1

    @pytest.mark.asyncio
    async def test_bulk_insert(self, engine):
        batch = [
            {"id": uuid.uuid4().hex, "entity_name": f"Bulk_{i}"} for i in range(50)
        ]
        count = await bulk_insert_nodes(engine, batch)
        assert count == 50

        nodes = await get_active_nodes(engine, agent_id=_DEFAULT_AGENT)
        assert len(nodes) == 50

    @pytest.mark.asyncio
    async def test_bulk_insert_empty(self, engine):
        count = await bulk_insert_nodes(engine, [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_find_by_name(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "Trademark_Infringement")
        result = await find_nodes_by_name(
            engine, ["trademark_infringement"], agent_id=_DEFAULT_AGENT
        )
        assert len(result) == 1
        assert result[0]["id"] == nid

    @pytest.mark.asyncio
    async def test_find_by_name_case_sensitive(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "CaseSensitive")
        result = await find_nodes_by_name(
            engine,
            ["casesensitive"],
            agent_id=_DEFAULT_AGENT,
            case_insensitive=False,
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_agent_scoped_query(self, engine):
        await insert_node(engine, uuid.uuid4().hex, "AgentA_Node", agent_id="agent_a")
        await insert_node(engine, uuid.uuid4().hex, "AgentB_Node", agent_id="agent_b")
        nodes = await get_active_nodes(engine, agent_id="agent_a")
        assert len(nodes) == 1
        assert nodes[0]["agent_id"] == "agent_a"


# ===================================================================
# Edge CRUD
# ===================================================================


class TestEdgeCRUD:
    @pytest.mark.asyncio
    async def test_insert_edge(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "A")
        await insert_node(engine, n2, "B")
        eid = await insert_edge(engine, uuid.uuid4().hex, n1, n2, "LINKED")
        edges = await get_active_edges(engine, agent_id=_DEFAULT_AGENT)
        assert any(e["id"] == eid for e in edges)

    @pytest.mark.asyncio
    async def test_soft_delete_edge(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "X")
        await insert_node(engine, n2, "Y")
        eid = uuid.uuid4().hex
        await insert_edge(engine, eid, n1, n2, "TEMP")
        await soft_delete_edge(engine, eid, agent_id=_DEFAULT_AGENT)
        edges = await get_active_edges(engine, agent_id=_DEFAULT_AGENT)
        assert not any(e["id"] == eid for e in edges)

    @pytest.mark.asyncio
    async def test_upsert_edge_creates_new(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "Src")
        await insert_node(engine, n2, "Tgt")
        eid = await upsert_edge(engine, uuid.uuid4().hex, n1, n2, "KNOWS", weight=1.0)
        edges = await get_active_edges(engine, agent_id=_DEFAULT_AGENT)
        assert any(e["id"] == eid for e in edges)

    @pytest.mark.asyncio
    async def test_upsert_edge_merges_weight(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "Src")
        await insert_node(engine, n2, "Tgt")
        eid1 = await upsert_edge(engine, uuid.uuid4().hex, n1, n2, "KNOWS", weight=1.0)
        eid2 = await upsert_edge(engine, uuid.uuid4().hex, n1, n2, "KNOWS", weight=2.5)
        # Same edge returned
        assert eid1 == eid2

        edges = await get_active_edges(engine, agent_id=_DEFAULT_AGENT)
        edge = [e for e in edges if e["id"] == eid1][0]
        assert edge["weight"] == pytest.approx(3.5)

    @pytest.mark.asyncio
    async def test_get_neighbors_outgoing(self, engine):
        n1, n2, n3 = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "Center")
        await insert_node(engine, n2, "Right")
        await insert_node(engine, n3, "Left")
        await insert_edge(engine, uuid.uuid4().hex, n1, n2, "POINTS_TO")
        await insert_edge(engine, uuid.uuid4().hex, n3, n1, "POINTS_TO")

        out = await get_neighbors(engine, n1, agent_id=_DEFAULT_AGENT, direction="out")
        assert len(out) == 1
        assert out[0]["target_id"] == n2

    @pytest.mark.asyncio
    async def test_get_neighbors_incoming(self, engine):
        n1, n2 = uuid.uuid4().hex, uuid.uuid4().hex
        await insert_node(engine, n1, "A")
        await insert_node(engine, n2, "B")
        await insert_edge(engine, uuid.uuid4().hex, n2, n1, "REF")

        inc = await get_neighbors(engine, n1, agent_id=_DEFAULT_AGENT, direction="in")
        assert len(inc) == 1
        assert inc[0]["source_id"] == n2


# ===================================================================
# FTS5 lexical pre-filtering
# ===================================================================


class TestFTS5:
    @pytest.mark.asyncio
    async def test_fts5_basic_search(self, engine):
        await insert_node(engine, uuid.uuid4().hex, "Trademark Infringement", "LEGAL")
        await insert_node(engine, uuid.uuid4().hex, "Patent Violation", "LEGAL")
        await insert_node(engine, uuid.uuid4().hex, "Revenue Forecast", "FINANCIAL")

        results = await fts5_search(engine, "trademark", agent_id=_DEFAULT_AGENT)
        assert len(results) == 1
        assert results[0]["entity_name"] == "Trademark Infringement"

    @pytest.mark.asyncio
    async def test_fts5_prefix_search(self, engine):
        await insert_node(engine, uuid.uuid4().hex, "Constitutional Law", "LEGAL")
        await insert_node(engine, uuid.uuid4().hex, "Contract Breach", "LEGAL")

        results = await fts5_search(engine, "con*", agent_id=_DEFAULT_AGENT)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_fts5_type_search(self, engine):
        await insert_node(engine, uuid.uuid4().hex, "Entity_A", "PERSON")
        await insert_node(engine, uuid.uuid4().hex, "Entity_B", "ORGANIZATION")

        results = await fts5_search(engine, "PERSON", agent_id=_DEFAULT_AGENT)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_fts5_empty_query(self, engine):
        results = await fts5_search(engine, "", agent_id=_DEFAULT_AGENT)
        assert results == []

    @pytest.mark.asyncio
    async def test_fts5_malformed_query_graceful(self, engine):
        # Should not raise — returns empty via graceful degradation
        results = await fts5_search(
            engine, '"""invalid syntax', agent_id=_DEFAULT_AGENT
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_fts5_sync_on_update(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "OriginalName", "ENTITY")

        # Update node name directly
        async with engine.connection() as db:
            await db.execute(
                "UPDATE nodes SET entity_name = ? WHERE id = ?",
                ("UpdatedName", nid),
            )
            await db.commit()

        old = await fts5_search(engine, "OriginalName", agent_id=_DEFAULT_AGENT)
        assert len(old) == 0

        new = await fts5_search(engine, "UpdatedName", agent_id=_DEFAULT_AGENT)
        assert len(new) == 1

    @pytest.mark.asyncio
    async def test_fts5_sync_on_delete(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "WillBeDeleted", "ENTITY")

        # Hard delete to trigger FTS5 delete trigger
        async with engine.connection() as db:
            await db.execute("DELETE FROM nodes WHERE id = ?", (nid,))
            await db.commit()

        results = await fts5_search(engine, "WillBeDeleted", agent_id=_DEFAULT_AGENT)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_fts5_rebuild(self, engine):
        await insert_node(engine, uuid.uuid4().hex, "RebuildTest", "ENTITY")
        await fts5_rebuild(engine)  # Should not raise
        results = await fts5_search(engine, "RebuildTest", agent_id=_DEFAULT_AGENT)
        assert len(results) == 1


# ===================================================================
# k-hop graph traversal
# ===================================================================


class TestKHopTraversal:
    @pytest.mark.asyncio
    async def test_k_hop_linear_chain(self, engine):
        # A -> B -> C -> D
        ids = [uuid.uuid4().hex for _ in range(4)]
        names = ["A", "B", "C", "D"]
        for nid, name in zip(ids, names):
            await insert_node(engine, nid, name)
        for i in range(3):
            await insert_edge(engine, uuid.uuid4().hex, ids[i], ids[i + 1], "NEXT")

        # 1-hop from A should find B only
        hop1 = await k_hop_neighbors(
            engine, ids[0], agent_id=_DEFAULT_AGENT, k=1, direction="out"
        )
        assert len(hop1) == 1
        assert hop1[0]["entity_name"] == "B"

        # 2-hop from A should find B and C
        hop2 = await k_hop_neighbors(
            engine, ids[0], agent_id=_DEFAULT_AGENT, k=2, direction="out"
        )
        assert len(hop2) == 2
        hop_names = {n["entity_name"] for n in hop2}
        assert hop_names == {"B", "C"}

    @pytest.mark.asyncio
    async def test_k_hop_bidirectional(self, engine):
        # A <-> B <-> C
        ids = [uuid.uuid4().hex for _ in range(3)]
        for nid, name in zip(ids, ["X", "Y", "Z"]):
            await insert_node(engine, nid, name)
        await insert_edge(engine, uuid.uuid4().hex, ids[0], ids[1], "BI")
        await insert_edge(engine, uuid.uuid4().hex, ids[1], ids[2], "BI")

        hop2 = await k_hop_neighbors(
            engine, ids[0], agent_id=_DEFAULT_AGENT, k=2, direction="both"
        )
        assert len(hop2) == 2

    @pytest.mark.asyncio
    async def test_k_hop_no_neighbors(self, engine):
        nid = uuid.uuid4().hex
        await insert_node(engine, nid, "Isolated")
        result = await k_hop_neighbors(engine, nid, agent_id=_DEFAULT_AGENT, k=3)
        assert result == []
