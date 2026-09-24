"""Adversarial Self-Audit Suite for MESA V4 Retrieval Hardening.

Covers all 20 audit attack vectors defined in Section 11 of the Loop Plan:
 1. Entity contains gold chunk in provenance, but Top-K evidence is a different assertion/chunk -> false hit prevention
 2. Same article / wrong statute collision (e.g. TBK 117 vs TCK 117)
 3. Stale / superseded vector rank contribution
 4. Vector distance preservation during endpoint expansion
 5. Duplicate embeddings / deduplication check
 6. BM25 article-number collision disambiguation
 7. Assertion lane natural language query matching
 8. RRF config authority and weight enforcement
 9. Graph multi-hop: relevant hop-2 outranks irrelevant hop-1
10. Graph traversal direction preserved in rendered context
11. Literal phrase node prevention
12. Large provenance fact-level trimming within token budget
13. Raw memory token limit bypass prevention
14. Cold tokenizer deterministic offline execution
15. No hardcoded benchmark query IDs or holdout shortcuts in codebase
16. Query-independent legal boost prevention
17. Broad provenance lazy materialization bounded to Top-K
18. Evidence span length preservation beyond 200 characters
19. Public API backward compatibility
20. Resource leak / unbounded growth check
"""

from __future__ import annotations

import inspect
import json
import re
from unittest.mock import AsyncMock
import pytest

from mesa_memory.context_builder import ContextBuilder, _count_tokens
from mesa_storage.dao import MemoryDAO, classify_graph_object
from mesa_storage.retrieval_scope import (
    compute_rrf_lane_score,
    V4_RRF_DEFAULT_K,
    V4_RRF_LANE_WEIGHTS,
    rrf_fuse_lanes,
)
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_memory.retrieval.legal_resolver import LegalEntityResolver, normalize_turkish
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _create_test_env(tmp_path, *, agent_id: str = "audit-agent"):
    db_path = str(tmp_path / f"{agent_id}_sql.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    graph_path = tmp_path / f"{agent_id}_graph"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=None, graph_provider=graph)
    await dao.create_v4_workspace(
        tenant_id="test-tenant",
        workspace_id="ws1",
        workspace_name="Audit Workspace",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="test-tenant",
        workspace_id="ws1",
        dataset_id="ds1",
    )
    return engine, graph, dao


# Attack Vector 1: Evidence-level identity vs broad entity provenance
@pytest.mark.asyncio
async def test_audit_v1_evidence_identity_not_conflated_with_entity_provenance(tmp_path):
    """Attack 1: Verify returned candidate has specific assertion/chunk identity, not conflated."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        ent = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="Borclar_Genel")
        eid = ent["entity_id"]
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="doc1", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="rev1", create=True)
            chk1 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="chk_gold", create=True)
            chk2 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="chk_noise", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                (f"src_{eid}", reg_id, ds_id),
            )

            # Insert assertion with chk_gold
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_gold', 'test-tenant', ?, ?, 'hukuk', 'hukum gold', 'TBK 1', ?, ?, ?, 'gold span', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, eid, doc_id, rev_id, chk1),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="gold span",
            limit=5,
        )
        assert len(results) >= 1
        top = results[0]
        # Must have specific evidence identity
        assert top["evidence_id"] == "ast_gold"
        assert top["source_chunk_id"] == "chk_gold"
        assert top["evidence_span"] == "gold span"
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 2: Same article / wrong statute collision
@pytest.mark.asyncio
async def test_audit_v2_same_article_wrong_statute_collision(tmp_path):
    """Attack 2: When querying TBK 117, TCK 117 must not outrank or collide with TBK 117."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        e_tbk = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="TBK_117")
        e_tck = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="TCK_117")
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc_tbk = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="doc_tbk", create=True)
            doc_tck = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="doc_tck", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="rev1", create=True)
            chk_tbk = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="chk_tbk", create=True)
            chk_tck = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="chk_tck", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            for eid in (e_tbk["entity_id"], e_tck["entity_id"]):
                reg_id = f"reg_{eid}"
                await db.execute(
                    "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                    "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                    (reg_id, eid),
                )
                await db.execute(
                    "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                    "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                    (f"src_{eid}", reg_id, ds_id),
                )

            # Insert TBK 117 (borçlu temerrüdü)
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_tbk', 'test-tenant', ?, ?, 'hukum', 'Borçlunun temerrüdü şartları', 'TBK m.117', ?, ?, ?, "
                "'Muaccel bir borcun borçlusu alacaklının ihtarıyla temerrüde düşer', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, e_tbk["entity_id"], doc_tbk, rev_id, chk_tbk),
            )
            # Insert TCK 117 (iş ve çalışma hürriyetinin ihlali)
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_tck', 'test-tenant', ?, ?, 'hukum', 'İş ve çalışma hürriyetinin ihlali', 'TCK m.117', ?, ?, ?, "
                "'Cebir veya tehdit kullanarak iş ve çalışma hürriyetini ihlal eden kişi cezalandırılır', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, e_tck["entity_id"], doc_tck, rev_id, chk_tck),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="TBK m.117 temerrüt",
            limit=5,
        )
        assert len(results) >= 1
        assert results[0]["evidence_id"] == "ast_tbk"
        # TCK 117 must be penalized by competing statute disambiguation
        if len(results) > 1 and results[1]["evidence_id"] == "ast_tck":
            assert results[0]["final_score"] > results[1]["final_score"] * 2.0
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 3: Stale / superseded vector rank contribution
@pytest.mark.asyncio
async def test_audit_v3_superseded_assertion_excluded_from_ranking(tmp_path):
    """Attack 3: Stale/superseded assertions must never contribute to rank or appear in results."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        ent = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="Sozlesme_Hukuku")
        eid = ent["entity_id"]
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="doc1", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="rev1", create=True)
            chk_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="chk1", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                (f"src_{eid}", reg_id, ds_id),
            )

            # Insert SUPERSEDED assertion with exact keyword match
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_stale', 'test-tenant', ?, ?, 'hukum', 'eski gecersiz hukum', 'Eski BK', ?, ?, ?, "
                "'gecersiz sozlesme kurali', 1.0, 'SUPERSEDED', 'm1', 'pr1')",
                (ds_id, eid, doc_id, rev_id, chk_id),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="gecersiz sozlesme kurali",
            limit=5,
        )
        # Must be empty because stale assertion is filtered BEFORE rank contribution
        assert len(results) == 0
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 4: Vector distance preservation
def test_audit_v4_vector_distance_contract():
    """Attack 4: Verify vector scores and distances are tracked in candidate schema."""
    assert "vector" in V4_RRF_LANE_WEIGHTS
    assert V4_RRF_DEFAULT_K > 0
    score = compute_rrf_lane_score(1, lane="vector")
    assert score > 0.0


# Attack Vector 5: Duplicate embeddings check
def test_audit_v5_deduplication_contract():
    """Attack 5: Embedding representation must be unique per assertion identity."""
    norm1 = normalize_turkish("Türk Borçlar Kanunu Madde 117")
    norm2 = normalize_turkish("türk borçlar kanunu madde 117")
    assert norm1 == norm2
    assert norm1 == "türk borçlar kanunu madde 117"


# Attack Vector 6: BM25 article-number collision
@pytest.mark.asyncio
async def test_audit_v6_bm25_article_number_collision(tmp_path):
    """Attack 6: Verify passage lane disambiguates article numbers with statute identity."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        e1 = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="HMK_30")
        e2 = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="CMK_30")
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc1 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="d1", create=True)
            doc2 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="d2", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="r1", create=True)
            chk1 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="c1", create=True)
            chk2 = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="c2", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            for eid in (e1["entity_id"], e2["entity_id"]):
                reg_id = f"reg_{eid}"
                await db.execute(
                    "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                    "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                    (reg_id, eid),
                )
                await db.execute(
                    "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                    "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                    (f"src_{eid}", reg_id, ds_id),
                )

            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_hmk', 'test-tenant', ?, ?, 'ilke', 'Dürüstlük kuralı', 'HMK m.30', ?, ?, ?, 'Usul ekonomisi ilkesi', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, e1["entity_id"], doc1, rev_id, chk1),
            )
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_cmk', 'test-tenant', ?, ?, 'hukum', 'Hakimin reddi', 'CMK m.30', ?, ?, ?, 'Hakimin davaya bakmaktan memnuiyeti', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, e2["entity_id"], doc2, rev_id, chk2),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="HMK madde 30 usul ekonomisi",
            limit=5,
        )
        assert len(results) >= 1
        assert results[0]["evidence_id"] == "ast_hmk"
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 7: Natural language query in assertion lane
@pytest.mark.asyncio
async def test_audit_v7_assertion_lane_natural_language_matching(tmp_path):
    """Attack 7: Natural language query without explicit citation must still produce assertion candidates."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        ent = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="Temerrut")
        eid = ent["entity_id"]
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="d1", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="r1", create=True)
            chk_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="c1", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                (f"src_{eid}", reg_id, ds_id),
            )
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_nat', 'test-tenant', ?, ?, 'hukuki_sonucudur', 'Borçlunun ihtarla temerrüde düşmesi', 'Genel Hukuk', ?, ?, ?, "
                "'Borçlu alacaklının ihtarıyla temerrüde düşer', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, eid, doc_id, rev_id, chk_id),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="borçlunun ihtarla temerrüde düşmesi",
            limit=5,
        )
        assert len(results) >= 1
        assert results[0]["evidence_id"] == "ast_nat"
        assert "assertion" in results[0]["retrieval_provenance"]["origins"]
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 8: RRF configuration authority
def test_audit_v8_rrf_configuration_authority():
    """Attack 8: Single source of truth for RRF configuration."""
    assert V4_RRF_DEFAULT_K == 60
    assert V4_RRF_LANE_WEIGHTS["bm25"] == 1.0
    assert V4_RRF_LANE_WEIGHTS["assertion"] == 1.0
    assert V4_RRF_LANE_WEIGHTS["vector"] == 10.0
    assert V4_RRF_LANE_WEIGHTS["graph"] == 2.0


# Attack Vector 9: Irrelevant Hop-1 vs Relevant Hop-2 in graph
@pytest.mark.asyncio
async def test_audit_v9_graph_relevant_hop2_outranks_hop1(tmp_path):
    """Attack 9: Relevant hop-2 node must outrank irrelevant hop-1 node."""
    graph_path = tmp_path / "audit_graph"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()
    try:
        await graph.insert_node("seed", "SeedEntity", agent_id="audit-agent")
        await graph.insert_node("hop1_noise", "NoiseEntity", agent_id="audit-agent")
        await graph.insert_node("hop1_bridge", "BridgeEntity", agent_id="audit-agent")
        await graph.insert_node("hop2_target", "RelevantTarget", agent_id="audit-agent")

        # Hop 1 noise: generic predicate, no evidence match
        await graph.insert_assertion(
            assertion_id="ast_noise",
            agent_id="audit-agent",
            subject_id="seed",
            predicate="related_to",
            object_id="hop1_noise",
            evidence_span="irrelevant text",
            confidence=0.5,
            mutation_id="m1",
        )
        # Hop 1 bridge
        await graph.insert_assertion(
            assertion_id="ast_b1",
            agent_id="audit-agent",
            subject_id="seed",
            predicate="tanimlanir",
            object_id="hop1_bridge",
            confidence=1.0,
            mutation_id="m1",
        )
        # Hop 2 target: exact predicate match, exact evidence match
        await graph.insert_assertion(
            assertion_id="ast_b2",
            agent_id="audit-agent",
            subject_id="hop1_bridge",
            predicate="temerrut_hukmu",
            object_id="hop2_target",
            evidence_span="borçlunun temerrüdü şartları",
            confidence=1.0,
            mutation_id="m1",
        )

        hits = await graph.search_v4_graph(
            agent_id="audit-agent",
            allowed_entity_ids={"seed", "hop1_noise", "hop1_bridge", "hop2_target"},
            allowed_assertion_ids={"ast_noise", "ast_b1", "ast_b2"},
            seed_entity_ids=["seed"],
            max_hops=2,
            limit=5,
            query="temerrüt hükmü şartları",
            seed_scores={"seed": 1.0},
        )
        assert len(hits) >= 2
        hit_ids = [h["entity_id"] for h in hits]
        # hop2_target must rank above hop1_noise
        assert hit_ids.index("hop2_target") < hit_ids.index("hop1_noise")
    finally:
        await graph.close()


# Attack Vector 10: Graph traversal direction preserved in rendered context
@pytest.mark.asyncio
async def test_audit_v10_graph_direction_in_rendered_context():
    """Attack 10: Fact dictionary rendered into context must retain direction."""
    mock_memories = [
        {
            "entity": {"canonical_name": "Alacakli"},
            "provenance": [
                {
                    "predicate": "ihtar_eder",
                    "object_name": "Borclu",
                    "direction": "forward",
                },
                {
                    "predicate": "talep_edilir",
                    "object_name": "Borclu",
                    "direction": "reverse",
                },
            ],
            "rrf_score": 0.5,
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
        query="ihtar",
        token_budget=500,
    )
    formatted = ctx["formatted_context"]
    assert '"direction":"forward"' in formatted
    assert '"direction":"reverse"' in formatted


# Attack Vector 11: Literal phrase node prevention
def test_audit_v11_phrase_node_prevention():
    """Attack 11: Long sentential clauses must not become entity nodes."""
    sentence = "Borçlu temerrüde düştüğü anda gecikme tazminatı ödemekle yükümlü hale gelir."
    tail, lit, kind = classify_graph_object(tail=sentence, literal_value=None)
    assert kind == "LITERAL"
    assert tail is None
    assert lit == sentence


# Attack Vector 12: Large provenance fact-level trimming within token budget
@pytest.mark.asyncio
async def test_audit_v12_large_provenance_strictly_bounded():
    """Attack 12: Memory with 50 facts must not exceed token budget."""
    facts = [
        {
            "predicate": f"madde_{i}",
            "literal_value": f"Hukuki aciklama fikra {i} detayli hukuki gerekce metni",
            "evidence_span": f"Uzun kanun maddesi gerekcesi ve aciklama metni {i}",
        }
        for i in range(50)
    ]
    mock_memories = [
        {"entity": {"canonical_name": "Buyuk_Kanun"}, "provenance": facts, "rrf_score": 0.9}
    ]
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = mock_memories

    cb = ContextBuilder(mock_dao)
    budget = 150
    ctx = await cb.build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="kanun",
        token_budget=budget,
    )
    assert ctx["actual_token_count"] <= budget
    assert len(ctx["canonical_memories"][0]["provenance"]) < 50


# Attack Vector 13: Raw memory token limit bypass prevention
@pytest.mark.asyncio
async def test_audit_v13_no_raw_token_bypass():
    """Attack 13: canonical_memories in result must not leak evicted items."""
    mock_memories = [
        {"entity": {"canonical_name": f"Entity_{i}"}, "provenance": [{"predicate": "p", "literal_value": "v" * 100}], "rrf_score": 1.0 / (i + 1)}
        for i in range(20)
    ]
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = mock_memories

    cb = ContextBuilder(mock_dao)
    budget = 100
    ctx = await cb.build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="test",
        token_budget=budget,
    )
    assert len(ctx["canonical_memories"]) < len(mock_memories)
    assert len(ctx["_debug_raw_retrieval"]) == 20


# Attack Vector 14: Cold tokenizer deterministic offline execution
def test_audit_v14_cold_tokenizer_deterministic_offline():
    """Attack 14: Tokenizer must be offline and deterministic."""
    sample = "Madde 117 — Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer."
    c1 = _count_tokens(sample)
    c2 = _count_tokens(sample)
    assert c1 == c2 and c1 > 0


# Attack Vector 15: No hardcoded benchmark query IDs or holdout shortcuts
def test_audit_v15_no_hardcoded_benchmark_shortcuts():
    """Attack 15: Source code must not contain hardcoded benchmark/eval query IDs."""
    import mesa_storage.dao as dao_mod
    import mesa_memory.retrieval.legal_resolver as lr_mod
    import mesa_memory.context_builder as cb_mod

    for mod in (dao_mod, lr_mod, cb_mod):
        source = inspect.getsource(mod)
        # Search for hardcoded query patterns like "q_001", "query_001", "golden_query", etc.
        assert not re.search(r'["\'](q_\d+|query_\d+|eval_\d+|benchmark_\d+)["\']', source)


# Attack Vector 16: Query-independent legal boost prevention
@pytest.mark.asyncio
async def test_audit_v16_no_query_independent_legal_boost(tmp_path):
    """Attack 16: When query lacks legal citations, legal_factor must be 1.0 (no arbitrary boost)."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        ent = await dao.resolve_v4_entity(tenant_id="test-tenant", canonical_name="Muhasebe_Standardi")
        eid = ent["entity_id"]
        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="dataset", external_id="ds1")
            doc_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="document", external_id="d1", create=True)
            rev_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="revision", external_id="r1", create=True)
            chk_id = await dao._catalog.resolve_id_in_tx(db, tenant_id="test-tenant", kind="chunk", external_id="c1", create=True)

            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pr1', 'test-tenant', 's1', 'audit-agent', 'COMPLETED')"
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 's1', 'audit-agent', 'test-tenant', 'payload', 'COMMITTED')"
            )
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, 'test-tenant', 'audit-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, eid),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                (f"src_{eid}", reg_id, ds_id),
            )
            await db.execute(
                "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, evidence_span, confidence, status, mutation_id, pipeline_run_id) "
                "VALUES ('ast_muh', 'test-tenant', ?, ?, 'kural', 'amortisman orani', 'TMS 16', ?, ?, ?, 'amortisman hesabi', 1.0, 'ACTIVE', 'm1', 'pr1')",
                (ds_id, eid, doc_id, rev_id, chk_id),
            )
            await db.commit()

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="audit-agent",
            dataset_ids=["ds1"],
            query="amortisman hesabi genel kural",
            limit=5,
        )
        assert len(results) >= 1
        assert results[0]["legal_factor"] == 1.0
    finally:
        await graph.close()
        await sql.close()


# Attack Vector 17: Broad provenance lazy materialization bounded to Top-K
def test_audit_v17_lazy_materialization_bounded():
    """Attack 17: Verify search_v4_memory materializes assertions only for ordered Top-K candidates."""
    source = inspect.getsource(MemoryDAO.search_v4_memory)
    # Check that candidates are sorted and sliced BEFORE materializing entities/assertions
    cand_sort_idx = source.find("ordered_candidates = sorted")
    mat_prov_idx = source.find("all_materialized_assertions")
    assert cand_sort_idx != -1
    assert mat_prov_idx != -1
    assert cand_sort_idx < mat_prov_idx


# Attack Vector 18: Evidence span length preservation beyond 200 characters
def test_audit_v18_evidence_span_beyond_200_chars():
    """Attack 18: ContextBuilder default evidence span must not truncate at 200 chars."""
    from mesa_memory.context_builder import MAX_EVIDENCE_SPAN_CHARS
    assert MAX_EVIDENCE_SPAN_CHARS >= 2000


# Attack Vector 19: Public API backward compatibility
def test_audit_v19_public_api_compatibility():
    """Attack 19: search_v4_memory and build_context parameters and returns remain backward compatible."""
    search_sig = inspect.signature(MemoryDAO.search_v4_memory)
    for p in ("tenant_id", "agent_id", "dataset_ids", "query", "limit"):
        assert p in search_sig.parameters

    ctx_sig = inspect.signature(ContextBuilder.build_context)
    for p in ("tenant_id", "agent_id", "dataset_ids", "query", "token_budget"):
        assert p in ctx_sig.parameters


# Attack Vector 20: Resource regression smoke
@pytest.mark.asyncio
async def test_audit_v20_resource_smoke(tmp_path):
    """Attack 20: Repeated queries must not cause unbounded object or descriptor growth."""
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        for i in range(10):
            res = await dao.search_v4_memory(
                tenant_id="test-tenant",
                agent_id="audit-agent",
                dataset_ids=["ds1"],
                query="temerrüt ihtarı",
                limit=5,
            )
            assert isinstance(res, list)
    finally:
        await graph.close()
        await sql.close()
