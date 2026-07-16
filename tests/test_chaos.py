"""MESA v0.4.1 — Phase 3B: Chaos Engineering — Saga Rollback Resilience Test.

Proves that the Atomic Dual-Write Saga pattern correctly rolls back
SQLite INSERT when the LanceDB vector upsert fails.  Uses unittest.mock to
force-crash ``VectorEngine.upsert``, then verifies the ``nodes`` table
contains NO orphaned relational record.

This is a *mathematical proof* that the compensating rollback works:
    IF  vec.upsert() raises Exception
    THEN  nodes WHERE id = <generated_node_id> IS NULL
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "chaos")
VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

AGENT_ID = "chaos-agent"


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db_path = os.path.join(TEST_DIR, f"chaos_{uid}.db")
    vec_path = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    sql = AsyncEngine(db_path, max_connections=2)
    vec = VectorEngine(vec_path, max_workers=1)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec)
    yield dao, sql, vec, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec.close())
    loop.close()


class TestSagaRollbackOnVectorFailure:
    """Prove the B-7 Saga pattern correctly compensates on LanceDB crash."""

    def test_lancedb_failure_triggers_sqlite_rollback(self, dao_env):
        """When VectorEngine.upsert raises, the SQLite INSERT must be rolled back.

        Proof:
          1. Patch VectorEngine.upsert to raise Exception("Simulated LanceDB Crash")
          2. Call dao.insert_memory() — expect it to propagate the exception
          3. Query the nodes table directly — the node MUST NOT exist
        """
        dao, sql, vec, loop = dao_env

        # Pre-determine the node_id so we can query it after the crash
        target_node_id = str(uuid.uuid4())

        # Force LanceDB to crash
        with patch.object(
            vec,
            "upsert",
            new_callable=AsyncMock,
            side_effect=Exception("Simulated LanceDB Crash"),
        ):
            # The DAO calls db.rollback() explicitly, then re-raises.
            # The transaction() context manager catches the re-raise and
            # attempts a second ROLLBACK, which raises OperationalError
            # because the transaction was already rolled back.
            # Either exception type proves the Saga fired.
            with pytest.raises(Exception):
                loop.run_until_complete(
                    dao.insert_memory(
                        AGENT_ID,
                        node_id=target_node_id,
                        entity_name="Orphan_Entity",
                        content="This should never be committed",
                        embedding=VEC8,
                    )
                )

        # === THE PROOF ===
        # Query SQLite directly — the node MUST NOT exist.
        # If it does, the Saga ROLLBACK failed and we have dangling data.
        async def _check_node_exists():
            async with sql.connection() as db:
                async with db.execute(
                    "SELECT id FROM nodes WHERE id = ?",
                    (target_node_id,),
                ) as cursor:
                    return await cursor.fetchone()

        row = loop.run_until_complete(_check_node_exists())
        assert row is None, (
            f"SAGA VIOLATION: SQLite node {target_node_id} exists after "
            f"VectorEngine.upsert crash — ROLLBACK did not execute"
        )

    def test_successful_insert_persists_after_saga(self, dao_env):
        """Control test: verify that a normal insert DOES persist.

        Without this control, a false pass on the chaos test could mean
        the DAO never writes anything at all.
        """
        dao, sql, _, loop = dao_env

        node_id = loop.run_until_complete(
            dao.insert_memory(
                AGENT_ID,
                entity_name="Persistent_Entity",
                content="This MUST be committed",
                embedding=VEC8,
            )
        )

        async def _check_node_exists():
            async with sql.connection() as db:
                async with db.execute(
                    "SELECT id FROM nodes WHERE id = ?",
                    (node_id,),
                ) as cursor:
                    return await cursor.fetchone()

        row = loop.run_until_complete(_check_node_exists())
        assert row is not None, (
            "Control failure: normal insert did not persist to SQLite — "
            "the DAO is fundamentally broken"
        )

    def test_bulk_insert_saga_rollback(self, dao_env):
        """Prove bulk_insert_memory also rolls back on vector failure.

        The bulk path uses bulk_upsert() on VectorEngine. If it crashes,
        ALL SQLite rows in the batch must be rolled back.
        """
        dao, sql, vec, loop = dao_env

        records = [
            {"entity_name": f"Bulk_{i}", "content": "bulk content", "embedding": VEC8}
            for i in range(5)
        ]

        with patch.object(
            vec,
            "bulk_upsert",
            new_callable=AsyncMock,
            side_effect=Exception("Simulated Bulk LanceDB Crash"),
        ):
            with pytest.raises(Exception):
                loop.run_until_complete(
                    dao.bulk_insert_memory(AGENT_ID, records=records)
                )

        # Verify ALL rows were rolled back — zero dangling records
        async def _count_nodes():
            async with sql.connection() as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM nodes WHERE agent_id = ?",
                    (AGENT_ID,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else -1

        count = loop.run_until_complete(_count_nodes())
        assert count == 0, (
            f"SAGA VIOLATION: {count} SQLite nodes survived after "
            f"bulk VectorEngine crash — ROLLBACK did not execute"
        )

    def test_purge_saga_rollback(self, dao_env):
        """Prove purge_memory rolls back if vector soft_delete fails.

        Insert a real node first, then crash the vector soft_delete.
        The SQLite soft-delete (invalid_at stamp) must be reverted.
        """
        dao, sql, vec, loop = dao_env

        # Insert a real node (both SQLite + LanceDB succeed)
        node_id = loop.run_until_complete(
            dao.insert_memory(
                AGENT_ID,
                entity_name="Purgeable_Entity",
                content="purgeable",
                embedding=VEC8,
            )
        )

        # Now crash the vector layer during purge
        with patch.object(
            vec,
            "soft_delete",
            new_callable=AsyncMock,
            side_effect=Exception("Simulated Purge Vector Crash"),
        ):
            with pytest.raises(Exception):
                loop.run_until_complete(dao.purge_memory(AGENT_ID, scope="agent"))

        # The node must still be ACTIVE (invalid_at IS NULL)
        async def _check_node_active():
            async with sql.connection() as db:
                async with db.execute(
                    "SELECT id, invalid_at FROM nodes WHERE id = ? AND agent_id = ?",
                    (node_id, AGENT_ID),
                ) as cursor:
                    return await cursor.fetchone()

        row = loop.run_until_complete(_check_node_active())
        assert row is not None, "Node vanished after failed purge"
        # row is an aiosqlite.Row — access by index
        assert row[1] is None, (
            f"SAGA VIOLATION: node {node_id} was soft-deleted (invalid_at={row[1]}) "
            f"despite vector soft_delete crash — ROLLBACK did not execute"
        )
