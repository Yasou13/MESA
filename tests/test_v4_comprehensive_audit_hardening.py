"""Comprehensive audit regression and adversarial verification tests for MESA Core.

Validates:
1. Projection generation manifest verification handles empty/None query results safely.
2. ContextBuilder preserves complete legal evidence spans (>2000 chars) without truncation.
3. Cross-agent fallback negative isolation (Agent A cannot see Agent B assertions).
4. Turkish dotted/dotless I search invariance across query and evidence fields.
5. Graph out-of-scope entity/assertion path strict rejection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mesa_memory.context_builder import ContextBuilder
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.projection_generations import (
    ProjectionGenerationRepository,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_projection_generation_manifest_null_row_handling(tmp_path):
    """Verify projection generation manifest checks handle empty tables safely without TypeError."""
    db_path = str(tmp_path / "sql.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    repo = ProjectionGenerationRepository(engine)
    async with engine.connection() as db:
        await db.execute(
            "INSERT INTO projection_generations ("
            "generation_id, generation_kind, lifecycle_state, "
            "vector_relative_path, graph_relative_path, provider_manifest_json"
            ") VALUES ('gen-1', 'REBUILD', 'ACTIVE', "
            "'generations/gen-1/vector.lance', 'generations/gen-1/kuzu_db', '{}')"
        )
        await db.execute(
            "INSERT OR REPLACE INTO projection_runtime (runtime_id, active_generation_id, fencing_token, updated_at) "
            "VALUES (1, 'gen-1', 0, CURRENT_TIMESTAMP)"
        )
        await db.commit()

    identity = {
        "embedding_space_id": "space-magibu-768",
        "provider": "magibu",
        "model": "alibayram/embeddingmagibu-200m",
        "model_revision": "rev-1",
        "version": "v4",
        "dimension": 768,
        "normalized": True,
    }
    # This must complete successfully without crashing on row indexing
    await repo.assert_active_embedding_identity(
        identity=identity,
    )
    await engine.close()


@pytest.mark.asyncio
async def test_context_builder_large_evidence_span_no_2000_char_truncation():
    """Verify ContextBuilder preserves long legal evidence spans (>2000 chars) intact."""
    long_legal_text = (
        "MADDE 1 - " + ("Bu kanun hükmü gereğince tarafların hak ve borçları korunur. " * 50)
    )
    assert len(long_legal_text) > 2500

    mock_memories = [
        {
            "candidate_id": "cand-1",
            "assertion_id": "ast-1",
            "document_id": "doc-legal-1",
            "source_chunk_id": "chk-1",
            "evidence_span": long_legal_text,
            "entity": {"canonical_name": "TBK Madde 1"},
            "raw_scores": {"assertion": 100.0},
            "rrf_score": 0.05,
            "legal_factor": 1.5,
            "final_score": 0.075,
            "provenance": [
                {
                    "assertion_id": "ast-1",
                    "subject_name": "TBK Madde 1",
                    "predicate": "tanımlar",
                    "literal_value": "Sözleşme şartları",
                    "evidence_span": long_legal_text,
                    "document_id": "doc-legal-1",
                    "chunk_id": "chk-1",
                    "source_ref": "TBK m.1",
                    "jurisdiction": "TR",
                }
            ],
            "matched_assertions": [],
            "supporting_assertions": [],
            "retrieval_provenance": {
                "origins": ["assertion"],
                "lane_ranks": {"assertion": 1},
                "raw_scores": {"assertion": 100.0},
            },
        }
    ]

    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = mock_memories

    cb = ContextBuilder(mock_dao)
    ctx = await cb.build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="Sözleşme",
        token_budget=4000,
        max_evidence_span_chars=None,
    )

    assert "canonical_memories" in ctx
    assert len(ctx["canonical_memories"]) == 1
    retained_prov = ctx["canonical_memories"][0]["provenance"]
    assert len(retained_prov) == 1
    # Evidence span must not be truncated at 2000 characters
    assert len(retained_prov[0]["evidence_span"]) == len(long_legal_text)
    assert retained_prov[0]["evidence_span"] == long_legal_text
    assert long_legal_text in ctx["formatted_context"]


@pytest.mark.asyncio
async def test_cross_agent_fallback_negative_isolation(tmp_path):
    """Negative isolation: Agent A querying an entity cannot retrieve Agent B's assertions."""
    db_path = str(tmp_path / "sql.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=None)
    tenant_id = "tenant-iso"
    agent_a = "agent-alpha"
    agent_b = "agent-bravo"
    dataset_id = "ds-iso"

    try:
        await dao.create_v4_workspace(
            tenant_id=tenant_id, workspace_id="ws1", workspace_name="Workspace 1"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id=tenant_id, workspace_id="ws1", dataset_id=dataset_id
        )

        # Entity is shared in tenant catalog
        entity = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name="Sözleşme")
        eid = entity["entity_id"]

        # Only Agent B has a committed mutation with an assertion for this entity
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            doc_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id="doc_b", create=True
            )
            rev_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="revision", external_id="rev_b", create=True
            )
            chk_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="chunk", external_id="chk_b", create=True
            )

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr-b', ?, 'sess-b', ?, 'COMPLETED')",
                (tenant_id, agent_b),
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, "
                "tenant_id, content_payload, state) "
                "VALUES ('mut-b-1', 'cand-b', 'sess-b', ?, ?, 'payload-b', 'COMMITTED')",
                (agent_b, tenant_id),
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_b, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES ('src-b-1', ?, 'mut-b-1', ?, 'ACTIVE')",
                (reg_id, ds_id),
            )
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, "
                "predicate, literal_value, source_ref, document_id, revision_id, chunk_id, "
                "evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast-b-secret', ?, ?, ?, 'hükmeder', 'Tazminat', 'TBK 1', ?, ?, ?, "
                "'Gizli sözleşme maddesi', 1.0, 'ACTIVE', 'mut-b-1', 'pr-b')",
                (tenant_id, ds_id, eid, doc_id, rev_id, chk_id),
            )
            await db.commit()

        # Agent A searches for "Sözleşme"
        results_a = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_a,
            dataset_ids=[dataset_id],
            query="Sözleşme Tazminat",
            limit=5,
        )

        # Negative assertion: Agent A must NEVER see Agent B's assertion
        assert len(results_a) == 0

        # Agent B searching the same query MUST see their own assertion
        results_b = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_b,
            dataset_ids=[dataset_id],
            query="Sözleşme Tazminat",
            limit=5,
        )
        assert len(results_b) == 1
        assert results_b[0]["assertion_id"] == "ast-b-secret"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_turkish_casefolding_evidence_search_invariance(tmp_path):
    """Verify Turkish dotted/dotless I search invariance in assertion evidence."""
    db_path = str(tmp_path / "sql.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=None)
    tenant_id = "tenant-tr"
    agent_id = "agent-tr"
    dataset_id = "ds-tr"

    try:
        await dao.create_v4_workspace(
            tenant_id=tenant_id, workspace_id="ws1", workspace_name="Workspace 1"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id=tenant_id, workspace_id="ws1", dataset_id=dataset_id
        )

        entity = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name="Kiracı")
        eid = entity["entity_id"]

        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
            )
            doc_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id="doc_tr", create=True
            )
            rev_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="revision", external_id="rev_tr", create=True
            )
            chk_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="chunk", external_id="chk_tr", create=True
            )

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr-tr', ?, 'sess-tr', ?, 'COMPLETED')",
                (tenant_id, agent_id),
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, "
                "tenant_id, content_payload, state) "
                "VALUES ('mut-tr-1', 'cand-tr', 'sess-tr', ?, ?, 'payload-tr', 'COMMITTED')",
                (agent_id, tenant_id),
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_id, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES ('src-tr-1', ?, 'mut-tr-1', ?, 'ACTIVE')",
                (reg_id, ds_id),
            )
            # Evidence has uppercase dotted Turkish İ: "İHTAR"
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, "
                "predicate, literal_value, source_ref, document_id, revision_id, chunk_id, "
                "evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast-tr-1', ?, ?, ?, 'bildirir', 'İHTARNAME', 'TBK 1', ?, ?, ?, "
                "'Kiracıya resmi İHTAR çekilmiştir.', 1.0, 'ACTIVE', 'mut-tr-1', 'pr-tr')",
                (tenant_id, ds_id, eid, doc_id, rev_id, chk_id),
            )
            await db.commit()

        # Query uses lowercase "ihtar"
        results = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query="kiracı ihtar",
            limit=5,
        )
        assert len(results) == 1
        assert results[0]["assertion_id"] == "ast-tr-1"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_graph_out_of_scope_boundary_rejection(tmp_path):
    """Negative security: Graph paths containing an out-of-scope entity or assertion are pruned."""
    db_path = str(tmp_path / "sql.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    graph_path = tmp_path / "graph"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=graph)
    tenant_id = "tenant-gr-sec"
    agent_id = "agent-gr-sec"
    dataset_in = "ds-in"
    dataset_out = "ds-out"

    try:
        await dao.create_v4_workspace(
            tenant_id=tenant_id, workspace_id="ws1", workspace_name="Workspace 1"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id=tenant_id, workspace_id="ws1", dataset_id=dataset_in
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id=tenant_id, workspace_id="ws1", dataset_id=dataset_out
        )

        ent_a = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name="NodeA")
        ent_b = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name="NodeB")
        ea_id, eb_id = ent_a["entity_id"], ent_b["entity_id"]

        # Put an assertion connecting NodeA to NodeB in OUT-OF-SCOPE dataset_out
        async with dao._sql.transaction() as db:
            ds_out_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="dataset", external_id=dataset_out
            )
            doc_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="document", external_id="doc_gr", create=True
            )
            rev_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="revision", external_id="rev_gr", create=True
            )
            chk_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="chunk", external_id="chk_gr", create=True
            )

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr-gr', ?, 'sess-gr', ?, 'COMPLETED')",
                (tenant_id, agent_id),
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, "
                "tenant_id, content_payload, state) "
                "VALUES ('mut-gr-1', 'cand-gr', 'sess-gr', ?, ?, 'payload-gr', 'COMMITTED')",
                (agent_id, tenant_id),
            )
            reg_id = f"reg_{ea_id}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_id, ea_id),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES ('src-gr-1', ?, 'mut-gr-1', ?, 'ACTIVE')",
                (reg_id, ds_out_id),
            )
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, "
                "predicate, object_entity_id, literal_value, source_ref, document_id, revision_id, chunk_id, "
                "evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast-out-of-scope', ?, ?, ?, 'bağlıdır', ?, NULL, 'REF', ?, ?, ?, "
                "'Gizli bağ kanıtı', 1.0, 'ACTIVE', 'mut-gr-1', 'pr-gr')",
                (tenant_id, ds_out_id, ea_id, eb_id, doc_id, rev_id, chk_id),
            )
            await db.commit()

        # Graph has entities A and B, and assertion ast-out-of-scope
        await graph.insert_node(node_id=ea_id, name="Node A", agent_id=agent_id)
        await graph.insert_node(node_id=eb_id, name="Node B", agent_id=agent_id)
        await graph.insert_assertion(
            assertion_id="ast-out-of-scope",
            subject_id=ea_id,
            object_id=eb_id,
            agent_id=agent_id,
            predicate="bağlıdır",
            mutation_id="mut-gr-1",
            source_ref="REF",
            evidence_span="Gizli bağ kanıtı",
        )

        # Query with ONLY dataset_in allowed
        results = await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_in],
            query="NodeA NodeB",
            graph_enabled=True,
            limit=5,
        )

        # Out of scope graph edge must NOT leak into results
        assert len(results) == 0
    finally:
        await graph.close()
        await engine.close()
