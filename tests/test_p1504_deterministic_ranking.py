"""P15-04 (D70): deterministic ranking tie-breaks across two independent fresh stores."""

from __future__ import annotations

from pathlib import Path

import pytest

from mesa_memory.config import config
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.retrieval_scope import rrf_fuse_lanes
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import process_projection_outbox_once

_CORPUS = [
    # Identical content across 4 documents -> guaranteed BM25, vector, and assertion ties
    ("doc-gamma", "rev-gamma-1", "chunk-gamma-0", 0, True, "Tied contract clause for deterministic tie-breaking verification."),
    ("doc-alpha", "rev-alpha-1", "chunk-alpha-0", 0, True, "Tied contract clause for deterministic tie-breaking verification."),
    ("doc-delta", "rev-delta-1", "chunk-delta-0", 0, True, "Tied contract clause for deterministic tie-breaking verification."),
    ("doc-beta", "rev-beta-1", "chunk-beta-0", 0, True, "Tied contract clause for deterministic tie-breaking verification."),
    # Multi-chunk document with identical chunk text -> tests chunk ordinal tie-break
    ("doc-multi", "rev-multi-1", "chunk-multi-2", 2, False, "Repeated multi-chunk clause for ordinal stability."),
    ("doc-multi", "rev-multi-1", "chunk-multi-0", 0, False, "Repeated multi-chunk clause for ordinal stability."),
    ("doc-multi", "rev-multi-1", "chunk-multi-1", 1, True, "Repeated multi-chunk clause for ordinal stability."),
    # Query with short 2-char Turkish tokens ("da", "de") and shared terms
    ("doc-tr-2", "rev-tr-2", "chunk-tr-2-0", 0, True, "Kira sözleşmesi fesih bildirimi süresi de yazılı yapılır."),
    ("doc-tr-1", "rev-tr-1", "chunk-tr-1-0", 0, True, "Kira sözleşmesi fesih bildirimi süresi de yazılı yapılır."),
]


async def _ingest_fresh_store(
    store_dir: Path,
    *,
    with_vectors: bool = True,
    with_triplets: bool = False,
) -> tuple[AsyncEngine, VectorEngine, MemoryDAO]:
    store_dir.mkdir(parents=True, exist_ok=True)
    sql = AsyncEngine(str(store_dir / "mesa.db"))
    await sql.initialize()
    await initialize_schema(sql)

    identity = EmbeddingIdentity(
        provider="test", model="det-embed", version="v1", dimension=4, normalized=True
    )
    # Identical unit vector for every chunk -> guaranteed exact cosine distance ties!
    emb_svc = EmbeddingService(
        identity=identity, provider_fn=lambda _text: [0.5, 0.5, 0.5, 0.5]
    )
    vec = VectorEngine(
        str(store_dir / "vector.lance"),
        max_workers=1,
        embedding_service=emb_svc,
    )
    await vec.initialize()

    dao = MemoryDAO(
        sqlite_engine=sql,
        vector_engine=vec,
        embedding_enabled=with_vectors,
    )
    await dao.initialize()
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-det", workspace_id="ws-det", dataset_id="ds-det"
    )
    session = await dao.create_v4_session(
        tenant_id="tenant-det",
        workspace_id="ws-det",
        dataset_ids=["ds-det"],
        agent_id="agent-det",
        principal_id="principal-det",
    )

    for doc_id, rev_id, chunk_id, ordinal, finalize_rev, content in _CORPUS:
        admitted = await dao.admit_v4_memory(
            tenant_id="tenant-det",
            workspace_id="ws-det",
            dataset_id="ds-det",
            agent_id="agent-det",
            session_id=session["session_id"],
            document_id=doc_id,
            revision_id=rev_id,
            chunk_id=chunk_id,
            title=doc_id,
            content_payload=content,
            source_ref=f"file://{doc_id}",
            evidence_span=content,
            revision_number=1,
            chunk_ordinal=ordinal,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            embedding_space_id=identity.embedding_space_id,
            embedding_normalized=identity.normalized,
            validation_mode=0,
            policy=config.queue_admission_policy,
            finalize_revision=finalize_rev,
        )
        mutation_id = admitted["response"]["mutation_id"]
        triplets = (
            [
                {
                    "head": "ContractClause",
                    "relation": "governs",
                    "tail": f"RuleTarget_{doc_id}",
                    "fact_text": content,
                    "confidence": 0.9,
                }
            ]
            if with_triplets
            else []
        )
        await dao.record_mutation_extraction("agent-det", mutation_id, triplets)
        await dao.set_mutation_state("agent-det", mutation_id, "VALIDATED")

    for _ in range(5):
        res = await process_projection_outbox_once(dao, worker_id="det-worker", limit=100)
        if res["completed"] == 0:
            break

    return sql, vec, dao


def _rank_signature(results: list[dict]) -> list[tuple]:
    return [
        (
            r["document_id"],
            r["source_chunk_id"],
            round(float(r["final_score"]), 12),
        )
        for r in results
    ]


@pytest.mark.asyncio
async def test_two_fresh_stores_produce_identical_ranks_across_all_lanes(tmp_path: Path):
    sql_a, vec_a, dao_a = await _ingest_fresh_store(
        tmp_path / "store_a", with_vectors=True, with_triplets=True
    )
    sql_b, vec_b, dao_b = await _ingest_fresh_store(
        tmp_path / "store_b", with_vectors=True, with_triplets=True
    )
    try:
        # Confirm physical IDs are deterministic across fresh stores for the same tenant
        # AND distinct across different tenants.
        async with sql_a.connection() as db_a, sql_b.connection() as db_b:
            phys_a = {
                row[0]: row[1]
                for row in await (
                    await db_a.execute(
                        "SELECT external_id, physical_id FROM v4_catalog_identities "
                        "WHERE kind = 'document'"
                    )
                ).fetchall()
            }
            phys_b = {
                row[0]: row[1]
                for row in await (
                    await db_b.execute(
                        "SELECT external_id, physical_id FROM v4_catalog_identities "
                        "WHERE kind = 'document'"
                    )
                ).fetchall()
            }
        assert phys_a == phys_b

        await dao_a.ensure_v4_catalog_scope(
            tenant_id="tenant-other", workspace_id="ws-det", dataset_id="ds-det"
        )
        async with sql_a.connection() as db_a:
            other_ds = await dao_a._catalog.resolve_id_in_tx(
                db_a, tenant_id="tenant-other", kind="dataset", external_id="ds-det"
            )
            det_ds = await dao_a._catalog.resolve_id_in_tx(
                db_a, tenant_id="tenant-det", kind="dataset", external_id="ds-det"
            )
        assert other_ds != det_ds

        queries = [
            "Tied contract clause deterministic verification",
            "Repeated multi-chunk clause ordinal stability",
            "Kira sözleşmesi fesih bildirimi süresi de yazılı",
            "ContractClause governs",
        ]
        weight_profiles = [
            {"vector": 8.0, "bm25": 1.0, "assertion": 0.0},
            {"vector": 0.0, "bm25": 1.0, "assertion": 0.0},
            {"vector": 0.0, "bm25": 0.0, "assertion": 1.0},
            {"vector": 1.0, "bm25": 1.0, "assertion": 1.0},
        ]

        for query in queries:
            for weights in weight_profiles:
                res_a = await dao_a.search_v4_memory(
                    tenant_id="tenant-det",
                    agent_id="agent-det",
                    dataset_ids=["ds-det"],
                    query=query,
                    limit=10,
                    rrf_weights=weights,
                )
                res_b = await dao_b.search_v4_memory(
                    tenant_id="tenant-det",
                    agent_id="agent-det",
                    dataset_ids=["ds-det"],
                    query=query,
                    limit=10,
                    rrf_weights=weights,
                )
                assert res_a, f"expected results for {query!r} ({weights})"
                assert _rank_signature(res_a) == _rank_signature(res_b), (
                    f"rank mismatch for query={query!r} weights={weights}\n"
                    f"A={_rank_signature(res_a)}\nB={_rank_signature(res_b)}"
                )

        # Verify expected stable order for tied documents and multi-chunk ordinals
        tied_docs = await dao_a.search_v4_memory(
            tenant_id="tenant-det",
            agent_id="agent-det",
            dataset_ids=["ds-det"],
            query="Tied contract clause deterministic verification",
            limit=10,
            rrf_weights={"vector": 0.0, "bm25": 1.0, "assertion": 0.0},
        )
        assert [r["document_id"] for r in tied_docs[:4]] == [
            "doc-alpha",
            "doc-beta",
            "doc-delta",
            "doc-gamma",
        ]

        multi_chunks = await dao_a.search_v4_memory(
            tenant_id="tenant-det",
            agent_id="agent-det",
            dataset_ids=["ds-det"],
            query="Repeated multi-chunk clause ordinal stability",
            limit=10,
            rrf_weights={"vector": 0.0, "bm25": 1.0, "assertion": 0.0},
        )
        assert [r["source_chunk_id"] for r in multi_chunks[:3]] == [
            "chunk-multi-0",
            "chunk-multi-1",
            "chunk-multi-2",
        ]
    finally:
        await vec_a.close()
        await vec_b.close()
        await sql_a.close()
        await sql_b.close()


def test_rrf_fuse_lanes_uses_custom_tie_break_key_without_changing_scores():
    # Two candidates with equal fused scores: random UUID order would put "aaa"
    # before "zzz", while stable key puts ("doc-1", 0) before ("doc-2", 0).
    stable = {"zzz-random-uuid": ("doc-1", 0), "aaa-random-uuid": ("doc-2", 0)}
    fused = rrf_fuse_lanes(
        {
            "bm25": ["zzz-random-uuid", "aaa-random-uuid"],
            "vector": ["aaa-random-uuid", "zzz-random-uuid"],
        },
        k=60,
        weights={"bm25": 1.0, "vector": 1.0},
        tie_break_key=stable.__getitem__,
    )
    assert [cid for cid, _, _ in fused] == ["zzz-random-uuid", "aaa-random-uuid"]
    assert fused[0][1] == pytest.approx(fused[1][1])


@pytest.mark.asyncio
async def test_legacy_random_physical_ids_still_tie_break_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Even when two stores carry different legacy UUID4 physical IDs, stable
    content-identity tie-breaks produce identical ranks across lanes."""
    import uuid as _uuid

    from mesa_storage.repositories import catalog as catalog_mod

    monkeypatch.setattr(catalog_mod.uuid, "uuid5", lambda _ns, _name: _uuid.uuid4())
    sql_a, vec_a, dao_a = await _ingest_fresh_store(
        tmp_path / "legacy_a", with_vectors=True, with_triplets=False
    )
    sql_b, vec_b, dao_b = await _ingest_fresh_store(
        tmp_path / "legacy_b", with_vectors=True, with_triplets=False
    )
    try:
        async with sql_a.connection() as db_a, sql_b.connection() as db_b:
            phys_a = dict(
                await (
                    await db_a.execute(
                        "SELECT external_id, physical_id FROM v4_catalog_identities "
                        "WHERE kind = 'document'"
                    )
                ).fetchall()
            )
            phys_b = dict(
                await (
                    await db_b.execute(
                        "SELECT external_id, physical_id FROM v4_catalog_identities "
                        "WHERE kind = 'document'"
                    )
                ).fetchall()
            )
        assert any(phys_a[k] != phys_b[k] for k in phys_a)

        for weights in (
            {"vector": 8.0, "bm25": 1.0, "assertion": 0.0},
            {"vector": 0.0, "bm25": 1.0, "assertion": 0.0},
            {"vector": 0.0, "bm25": 0.0, "assertion": 1.0},
        ):
            res_a = await dao_a.search_v4_memory(
                tenant_id="tenant-det",
                agent_id="agent-det",
                dataset_ids=["ds-det"],
                query="Tied contract clause deterministic verification",
                limit=10,
                rrf_weights=weights,
            )
            res_b = await dao_b.search_v4_memory(
                tenant_id="tenant-det",
                agent_id="agent-det",
                dataset_ids=["ds-det"],
                query="Tied contract clause deterministic verification",
                limit=10,
                rrf_weights=weights,
            )
            assert _rank_signature(res_a) == _rank_signature(res_b)
    finally:
        await vec_a.close()
        await vec_b.close()
        await sql_a.close()
        await sql_b.close()
