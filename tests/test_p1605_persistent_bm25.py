"""Tests for D80: persistent per-(tenant, dataset) BM25 FTS5 index."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from mesa_storage.dao import MemoryDAO, _dataset_fts_table_name
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from tests.test_document_ingestion import _add
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_v4_retrieval_hardening_phase1 import _create_committed_mutation


async def _make_env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "p1605.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    dao_persistent = MemoryDAO(sql, vector, persistent_dataset_fts_enabled=True)
    dao_temp = MemoryDAO(sql, vector, persistent_dataset_fts_enabled=False)
    return sql, dao_persistent, dao_temp


def _trace_sql(monkeypatch):
    statements: list[str] = []
    orig_execute = aiosqlite.Connection.execute

    def traced(self, sql, parameters=None):
        statements.append(str(sql))
        return orig_execute(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", traced)
    return statements


@pytest.mark.asyncio
async def test_persistent_dataset_fts_identical_ranks_and_scores(
    tmp_path, monkeypatch
):
    sql, dao_persistent, dao_temp = await _make_env(tmp_path)
    try:
        docs = [
            (
                "doc-1",
                "chunk-1",
                "TBK m. 314 kira bedeli ödeme süresi ve kiracının temerrüdü.",
                "TBK m. 314",
                "regulates",
                "kira bedeli",
            ),
            (
                "doc-2",
                "chunk-2",
                "TBK m. 315 kiracıya yazılı ihtar ve otuz günlük ödeme süresi.",
                "TBK m. 315",
                "regulates",
                "otuz gün",
            ),
            (
                "doc-3",
                "chunk-3",
                "TBK m. 315 kiracıya yazılı ihtar ve otuz günlük ödeme süresi.",
                "TBK m. 315",
                "regulates",
                "otuz gün",
            ),
            (
                "doc-4",
                "chunk-4",
                "Veri koruma görevlisi gizlilik denetimi ve uyumluluk raporu hazırlar.",
                "Veri Koruma",
                "requires",
                "uyumluluk",
            ),
        ]
        for idx, (doc_id, chunk_id, text, subj, pred, lit) in enumerate(docs, start=1):
            await _create_committed_mutation(
                dao_persistent,
                raw_log_id=idx,
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_id="dataset-a",
                document_id=doc_id,
                chunk_id=chunk_id,
                content=text,
                subject=subj,
                predicate=pred,
                literal_value=lit,
                evidence_span=text,
            )

        queries = [
            "kira bedeli ödeme",
            "kiracı temerrüt ihtar",
            "otuz günlük süre",
            "gizlilik denetimi uyumluluk",
            "TBK 315",
        ]
        statements = _trace_sql(monkeypatch)
        for query in queries:
            meta_p, meta_t = {}, {}
            res_p = await dao_persistent.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_ids=["dataset-a"],
                query=query,
                certification_metadata=meta_p,
            )
            res_t = await dao_temp.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_ids=["dataset-a"],
                query=query,
                certification_metadata=meta_t,
            )
            assert res_p == res_t
            assert meta_p == meta_t
            assert [r["assertion_id"] for r in res_p] == [
                r["assertion_id"] for r in res_t
            ]
            assert [r["retrieval_provenance"]["raw_scores"] for r in res_p] == [
                r["retrieval_provenance"]["raw_scores"] for r in res_t
            ]

        # Confirm persistent table exists and a warm dataset-wide search does not
        # build temp.v4_scoped_assertions_fts.
        statements.clear()
        await dao_persistent.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="kira bedeli",
        )
        assert not any(
            "CREATE VIRTUAL TABLE temp.v4_scoped_assertions_fts" in s
            for s in statements
        )
        async with sql.connection() as db:
            phys_ds = await dao_persistent.catalog.resolve_id_in_tx(
                db, tenant_id="tenant-a", kind="dataset", external_id="dataset-a"
            )
            table_name = _dataset_fts_table_name("tenant-a", phys_ds)
            async with db.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ) as cur:
                assert await cur.fetchone() is not None
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_narrower_scopes_use_temp_fts_and_never_share_index(
    tmp_path, monkeypatch
):
    sql, dao_persistent, dao_temp = await _make_env(tmp_path)
    try:
        # Distinct table names per (tenant, dataset)
        assert _dataset_fts_table_name("t1", "d1") != _dataset_fts_table_name(
            "t1", "d2"
        )
        assert _dataset_fts_table_name("t1", "d1") != _dataset_fts_table_name(
            "t2", "d1"
        )

        await _create_committed_mutation(
            dao_persistent,
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            document_id="doc-a1",
            chunk_id="chunk-a1",
            content="Article 12: Data protection officer oversees compliance audits.",
            subject="Article 12",
            predicate="requires",
            literal_value="compliance audits",
            evidence_span="Data protection officer oversees compliance audits.",
        )
        async with sql.transaction() as db:
            await db.execute("UPDATE v4_assertions SET jurisdiction = 'TR'")
            await db.commit()
        await _create_committed_mutation(
            dao_persistent,
            raw_log_id=2,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-b",
            document_id="doc-b1",
            chunk_id="chunk-b1",
            content="Article 18: Secondary dataset compliance reporting obligation.",
            subject="Article 18",
            predicate="requires",
            literal_value="compliance reporting",
            evidence_span="Secondary dataset compliance reporting obligation.",
        )

        baseline = await dao_persistent.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="compliance officer",
        )
        assert len(baseline) == 1

        # 1. Filtered scopes (jurisdiction, valid_at, multi-dataset) must use temp FTS.
        for extra_kwargs in (
            {"dataset_ids": ["dataset-a"], "jurisdiction": "TR"},
            {"dataset_ids": ["dataset-a"], "valid_at": "2026-01-01"},
            {"dataset_ids": ["dataset-a", "dataset-b"]},
        ):
            statements = _trace_sql(monkeypatch)
            res_p = await dao_persistent.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                query="compliance",
                **extra_kwargs,
            )
            assert any(
                "CREATE VIRTUAL TABLE temp.v4_scoped_assertions_fts" in s
                for s in statements
            ), f"expected temp FTS for narrower scope {extra_kwargs}"
            res_t = await dao_temp.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                query="compliance",
                **extra_kwargs,
            )
            assert res_p == res_t

        # 2. Adding a second agent to dataset-a makes agent-a's scope narrower
        # than the full dataset; agent-a must fall back to temp FTS and retain
        # bit-for-bit identical BM25 scores and ranks.
        for idx in range(10, 25):
            await _create_committed_mutation(
                dao_persistent,
                raw_log_id=idx,
                tenant_id="tenant-a",
                agent_id="agent-b",
                dataset_id="dataset-a",
                document_id=f"doc-other-{idx}",
                chunk_id=f"chunk-other-{idx}",
                content=f"Other agent {idx}: compliance compliance officer officer override.",
                subject="Other Agent",
                predicate="overrides",
                literal_value="compliance officer",
                evidence_span="compliance compliance officer officer override.",
            )

        statements = _trace_sql(monkeypatch)
        after_multi_agent = await dao_persistent.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="compliance officer",
        )
        assert any(
            "CREATE VIRTUAL TABLE temp.v4_scoped_assertions_fts" in s
            for s in statements
        )
        assert after_multi_agent == baseline
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_transactional_sync_commit_supersede_rollback_and_purge(model_runtime):
    async with model_runtime() as env:
        async def _dataset_fts_contents() -> list[str]:
            async with env.dao._sql.connection() as db:
                phys_ds = await env.dao.catalog.resolve_id_in_tx(
                    db,
                    tenant_id="model-tenant",
                    kind="dataset",
                    external_id="main",
                )
                table_name = _dataset_fts_table_name("model-tenant", phys_ds)
                async with db.execute(
                    f"SELECT evidence_span FROM main.{table_name}"
                ) as cur:
                    return [str(r[0]) for r in await cur.fetchall()]

        # 1. Commit v1: persistent dataset FTS is updated in the commit tx (before any search).
        await _add(env, "Durable alpha regulation text.")
        await _consume(env, 3)
        spans = await _dataset_fts_contents()
        assert any("alpha regulation" in s for s in spans)

        # 2. Supersede v1 with v2: persistent dataset FTS drops v1 and adds v2 in the commit tx.
        await _add(env, "Durable beta amendment text.")
        await _consume(env, 3)
        spans = await _dataset_fts_contents()
        assert any("beta amendment" in s for s in spans)
        assert not any("alpha regulation" in s for s in spans)

        # 3. Roll back v2: persistent dataset FTS drops v2 and restores v1 in the rollback tx.
        async with env.dao._sql.connection() as db:
            async with db.execute(
                "SELECT pipeline_run_id FROM memory_mutations ORDER BY rowid DESC LIMIT 1"
            ) as cur:
                run_id = str((await cur.fetchone())[0])
        await env.dao.request_pipeline_rollback(run_id)
        spans = await _dataset_fts_contents()
        assert any("alpha regulation" in s for s in spans)
        assert not any("beta amendment" in s for s in spans)

        # 4. Purge document: persistent dataset FTS drops all purged rows in the purge tx.
        await env.dao.purge_v4_document(
            tenant_id="model-tenant",
            dataset_id="main",
            document_id="whole-document",
        )
        spans = await _dataset_fts_contents()
        assert spans == []


@pytest.mark.asyncio
async def test_missing_dataset_fts_rebuilds_from_sqlite_on_first_search(
    tmp_path, monkeypatch
):
    sql, dao_persistent, dao_temp = await _make_env(tmp_path)
    try:
        await _create_committed_mutation(
            dao_persistent,
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            document_id="doc-1",
            chunk_id="chunk-1",
            content="Kira sözleşmesi fesih bildirimi otuz gün önceden yapılır.",
            subject="Kira Sözleşmesi",
            predicate="requires",
            literal_value="otuz gün",
            evidence_span="Kira sözleşmesi fesih bildirimi otuz gün önceden yapılır.",
        )

        # Drop any persistent table to simulate an upgraded pre-P16-05 store.
        async with sql.transaction() as db:
            phys_ds = await dao_persistent.catalog.resolve_id_in_tx(
                db, tenant_id="tenant-a", kind="dataset", external_id="dataset-a"
            )
            table_name = _dataset_fts_table_name("tenant-a", phys_ds)
            await db.execute(f"DROP TABLE IF EXISTS main.{table_name}")
            await db.commit()

        expected = await dao_temp.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="fesih bildirimi otuz gün",
        )
        actual = await dao_persistent.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="fesih bildirimi otuz gün",
        )
        assert actual == expected
        assert len(actual) == 1

        # Verify table was rebuilt and second search reuses it without temp FTS.
        statements = _trace_sql(monkeypatch)
        actual_again = await dao_persistent.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="kira sözleşmesi",
        )
        assert len(actual_again) == 1
        assert not any(
            "CREATE VIRTUAL TABLE temp.v4_scoped_assertions_fts" in s
            for s in statements
        )
        assert not any(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS main.{table_name}" in s
            for s in statements
        )
    finally:
        await sql.close()
