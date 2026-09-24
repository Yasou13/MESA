"""Phase 9 regression tests: Graph Retrieval / Relevance Hardening.

Verifies all 10 Phase 9 requirements:
1. correct seed -> useful path
2. wrong seed does not dominate
3. hop-1 irrelevant vs hop-2 relevant
4. multiple support paths
5. direction-sensitive relation
6. scoped graph
7. limit best candidate not arbitrarily dropped (relevance-safe limiting)
8. graph participation in fusion
9. positive utility
10. harmful graph candidate detected
"""

from __future__ import annotations

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema
from mesa_storage.retrieval_scope import V4_RRF_LANE_WEIGHTS


async def _create_graph_env(tmp_path, *, agent_id: str = "test-agent"):
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
        workspace_name="Workspace 1",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="test-tenant",
        workspace_id="ws1",
        dataset_id="ds1",
    )
    return engine, graph, dao


async def _ingest_test_assertion(
    dao: MemoryDAO,
    graph: KuzuGraphProvider,
    *,
    mutation_id: str,
    subject_name: str,
    predicate: str,
    object_name: str,
    confidence: float = 1.0,
    evidence_span: str = "",
    jurisdiction: str = "TR",
    status: str = "ACTIVE",
    tenant_id: str = "test-tenant",
    agent_id: str = "test-agent",
    dataset_id: str = "ds1",
) -> tuple[str, str, str]:
    s_entity = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name=subject_name)
    o_entity = await dao.resolve_v4_entity(tenant_id=tenant_id, canonical_name=object_name)
    s_id = s_entity["entity_id"]
    o_id = o_entity["entity_id"]

    async with dao._sql.transaction() as db:
        ds_id = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )
        pipe_id = f"pipe_{mutation_id}"
        await db.execute(
            "INSERT OR IGNORE INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'sess_1', ?, 'COMPLETED')",
            (pipe_id, tenant_id, agent_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
            "VALUES (?, ?, 'sess_1', ?, ?, 'payload', 'COMMITTED')",
            (mutation_id, f"cand_{mutation_id}", agent_id, tenant_id),
        )
        for eid in (s_id, o_id):
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT OR IGNORE INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_id, eid),
            )
            await db.execute(
                "INSERT OR IGNORE INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, ?, ?, 'ACTIVE')",
                (f"src_{eid}_{mutation_id}", reg_id, mutation_id, ds_id),
            )

        a_id = f"ast_{mutation_id}_{s_id}_{o_id}"
        await db.execute(
            "INSERT OR IGNORE INTO v4_assertions ("
            "assertion_id, tenant_id, dataset_id, subject_id, predicate, object_entity_id, "
            "source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id, "
            "evidence_span, jurisdiction"
            ") VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', ?, ?, ?, ?, ?, ?)",
            (a_id, tenant_id, ds_id, s_id, predicate, o_id, confidence, status, mutation_id, pipe_id, evidence_span, jurisdiction),
        )
        reg_aid = f"reg_vec_{a_id}"
        await db.execute(
            "INSERT OR IGNORE INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
            "VALUES (?, ?, ?, 'canonical', 'ASSERTION_VECTOR', ?, 'ACTIVE')",
            (reg_aid, tenant_id, agent_id, a_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
            "VALUES (?, ?, ?, ?, 'ACTIVE')",
            (f"src_vec_{a_id}_{mutation_id}", reg_aid, mutation_id, ds_id),
        )
        await db.commit()

    # Ingest into Kùzu
    await graph.insert_node(s_id, subject_name, agent_id=agent_id)
    await graph.insert_node(o_id, object_name, agent_id=agent_id)
    await graph.insert_assertion(
        assertion_id=a_id,
        agent_id=agent_id,
        subject_id=s_id,
        predicate=predicate,
        object_id=o_id,
        confidence=confidence,
        evidence_span=evidence_span,
        jurisdiction=jurisdiction,
        status=status,
        mutation_id=mutation_id,
    )
    return s_id, o_id, a_id


# ---------------------------------------------------------------------------
# Test Cases (1 to 10)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_p9_1_correct_seed_useful_path(tmp_path):
    """1. Correct seed yields a useful multi-hop path."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        s1, o1, a1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="TBK_117", predicate="duzenler", object_name="Temerrut",
            evidence_span="TBK m.117 borçlunun temerrüdünü düzenler"
        )
        s2, o2, a2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="Temerrut", predicate="gerektirir", object_name="Ihtar",
            evidence_span="Temerrüt kural olarak ihtar gerektirir"
        )
        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[s1],
            allowed_entity_ids={s1, o1, o2},
            allowed_assertion_ids={a1, a2},
            max_hops=2,
            limit=10,
        )
        hit_eids = [h["entity_id"] for h in hits]
        assert o1 in hit_eids
        assert o2 in hit_eids
        assert hits[0]["score"] > 0
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_2_wrong_seed_does_not_dominate(tmp_path):
    """2. A wrong seed (low relevance) does not dominate a correct seed (high relevance)."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        # Good seed -> Good target (hop 2)
        g_seed, g_mid, a_g1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="GoodSeed", predicate="leads", object_name="GoodMid"
        )
        _, g_target, a_g2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="GoodMid", predicate="operates", object_name="GoodTarget",
            evidence_span="GoodTarget operates correctly"
        )
        # Bad seed -> Bad target (hop 1)
        b_seed, b_target, a_b1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m3", subject_name="BadSeed", predicate="relates", object_name="BadTarget"
        )

        seed_scores = {g_seed: 1.0, b_seed: 0.1}
        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[g_seed, b_seed],
            allowed_entity_ids={g_seed, g_mid, g_target, b_seed, b_target},
            allowed_assertion_ids={a_g1, a_g2, a_b1},
            max_hops=2,
            limit=10,
            seed_scores=seed_scores,
            query="operates",
        )
        hit_names = [h["entity_name"] for h in hits]
        assert "GoodTarget" in hit_names
        # GoodTarget (from GoodSeed) must outrank BadTarget (from BadSeed)
        good_rank = hit_names.index("GoodTarget")
        bad_rank = hit_names.index("BadTarget")
        assert good_rank < bad_rank
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_3_hop1_irrelevant_vs_hop2_relevant(tmp_path):
    """3. Highly relevant hop-2 candidate outranks irrelevant hop-1 candidate."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        seed, irr_target, a_irr = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="RootLaw", predicate="has_metadata", object_name="IrrelevantNoise",
            evidence_span="metadata noise item"
        )
        _, bridge, a_b = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="RootLaw", predicate="defines", object_name="ContractBridge"
        )
        _, rel_target, a_rel = await _ingest_test_assertion(
            dao, graph, mutation_id="m3", subject_name="ContractBridge", predicate="alacakli_temerrudu", object_name="DefaultCreditor",
            evidence_span="alacaklının temerrüdü şartları ve sonuçları"
        )

        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[seed],
            allowed_entity_ids={seed, irr_target, bridge, rel_target},
            allowed_assertion_ids={a_irr, a_b, a_rel},
            max_hops=2,
            limit=10,
            query="alacaklı temerrüdü şartları",
        )
        hit_names = [h["entity_name"] for h in hits]
        assert "DefaultCreditor" in hit_names
        assert "IrrelevantNoise" in hit_names
        # Relevant hop-2 candidate must outrank irrelevant hop-1
        assert hit_names.index("DefaultCreditor") < hit_names.index("IrrelevantNoise")
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_4_multiple_support_paths(tmp_path):
    """4. Target with multiple independent support paths achieves higher score than single-path target."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        # MultiTarget reached via Path A and Path B
        s, a_mid, a1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="Start", predicate="connects", object_name="BridgeA"
        )
        _, m_target, a2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="BridgeA", predicate="connects", object_name="MultiTarget"
        )
        _, b_mid, b1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m3", subject_name="Start", predicate="connects", object_name="BridgeB"
        )
        _, _, b2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m4", subject_name="BridgeB", predicate="connects", object_name="MultiTarget"
        )

        # SoloTarget reached via single path
        _, c_mid, c1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m5", subject_name="Start", predicate="connects", object_name="BridgeC"
        )
        _, s_target, c2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m6", subject_name="BridgeC", predicate="connects", object_name="SoloTarget"
        )

        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[s],
            allowed_entity_ids={s, a_mid, b_mid, c_mid, m_target, s_target},
            allowed_assertion_ids={a1, a2, b1, b2, c1, c2},
            max_hops=2,
            limit=10,
        )
        hits_by_name = {h["entity_name"]: h for h in hits}
        assert "MultiTarget" in hits_by_name
        assert "SoloTarget" in hits_by_name

        multi_hit = hits_by_name["MultiTarget"]
        solo_hit = hits_by_name["SoloTarget"]
        assert multi_hit["support_count"] >= 2
        assert multi_hit["score"] > solo_hit["score"]
        assert set(multi_hit["path_assertion_ids"]) == {a1, a2, b1, b2}
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_5_direction_sensitive_relation(tmp_path):
    """5. Forward traversal preserves semantic direction (Subject -> Predicate -> Object)."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        # Alice (subject) leads Aurora (object)
        alice, aurora, a1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="Alice", predicate="leads", object_name="Aurora"
        )
        # Bob (subject) leads Alice (object)
        bob, _, a2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="Bob", predicate="leads", object_name="Alice"
        )

        # Searching forward from Alice: only Aurora should be returned as outgoing object
        forward_hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[alice],
            allowed_entity_ids={alice, aurora, bob},
            allowed_assertion_ids={a1, a2},
            max_hops=1,
            direction="forward",
        )
        forward_names = [h["entity_name"] for h in forward_hits]
        assert "Aurora" in forward_names
        assert "Bob" not in forward_names

        # Searching with direction='any': forward hit receives top directional weight
        any_hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[alice],
            allowed_entity_ids={alice, aurora, bob},
            allowed_assertion_ids={a1, a2},
            max_hops=1,
            direction="any",
        )
        any_names = [h["entity_name"] for h in any_hits]
        assert any_names[0] == "Aurora"
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_6_scoped_graph(tmp_path):
    """6. Graph traversal strictly respects allowed_entity_ids and allowed_assertion_ids."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        s, mid, a1 = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="Alice", predicate="connects", object_name="GhostNode"
        )
        _, target, a2 = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="GhostNode", predicate="connects", object_name="TargetNode"
        )

        # GhostNode is NOT in allowed_entity_ids (e.g. deleted or out of scope)
        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[s],
            allowed_entity_ids={s, target},  # GhostNode excluded!
            allowed_assertion_ids={a1, a2},
            max_hops=2,
            limit=10,
        )
        assert len(hits) == 0

        # a2 is NOT in allowed_assertion_ids (e.g. superseded or expired)
        hits_filtered_assertion = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[s],
            allowed_entity_ids={s, mid, target},
            allowed_assertion_ids={a1},  # a2 excluded!
            max_hops=2,
            limit=10,
        )
        hit_names = [h["entity_name"] for h in hits_filtered_assertion]
        assert "TargetNode" not in hit_names
        assert "GhostNode" in hit_names
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_7_limit_best_candidate_not_dropped(tmp_path):
    """7. Backend LIMIT does not arbitrarily drop the highest-relevance candidate."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        s, _, _ = await _ingest_test_assertion(
            dao, graph, mutation_id="m0", subject_name="Root", predicate="dummy", object_name="SeedTarget"
        )
        allowed_entities = {s}
        allowed_assertions = set()

        # Ingest 15 low-relevance 1-hop nodes
        for i in range(1, 16):
            _, low_target, low_aid = await _ingest_test_assertion(
                dao, graph, mutation_id=f"low_{i}", subject_name="Root", predicate="noise", object_name=f"Noise_{i}",
                confidence=0.5
            )
            allowed_entities.add(low_target)
            allowed_assertions.add(low_aid)

        # Ingest 1 high-relevance 2-hop node with exact query match
        _, bridge, a_b = await _ingest_test_assertion(
            dao, graph, mutation_id="m_b", subject_name="Root", predicate="routes", object_name="PrimeBridge",
            confidence=1.0
        )
        _, prime, a_p = await _ingest_test_assertion(
            dao, graph, mutation_id="m_p", subject_name="PrimeBridge", predicate="critical_rule", object_name="GoldenTarget",
            confidence=1.0, evidence_span="critical_rule must be prioritized"
        )
        allowed_entities.update({bridge, prime})
        allowed_assertions.update({a_b, a_p})

        # Query with small limit=5
        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[s],
            allowed_entity_ids=allowed_entities,
            allowed_assertion_ids=allowed_assertions,
            max_hops=2,
            limit=5,
            query="critical_rule",
        )
        assert len(hits) <= 5
        hit_names = [h["entity_name"] for h in hits]
        assert "GoldenTarget" in hit_names
        assert hit_names[0] == "GoldenTarget"
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_8_graph_participation_in_fusion(tmp_path):
    """8. Graph lane participates in RRF fusion and contributes rank and provenance."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="Alice", predicate="connects", object_name="Aurora"
        )
        await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="Aurora", predicate="uses", object_name="HeliosDB"
        )

        results = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["ds1"],
            query="Alice",
            limit=10,
        )
        helios_matches = [r for r in results if r["entity"]["canonical_name"] == "HeliosDB"]
        assert len(helios_matches) == 1
        helios = helios_matches[0]
        assert "graph" in helios["retrieval_provenance"]["origins"]
        assert helios["retrieval_provenance"]["lane_ranks"]["graph"] >= 1
        assert helios["rrf_score"] > 0
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_9_positive_utility(tmp_path):
    """9. Multi-hop entity without direct lexical mention is retrieved with graph, but absent without graph."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="SourceEntity", predicate="links", object_name="MiddleEntity"
        )
        await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="MiddleEntity", predicate="operates", object_name="HiddenCrownJewel"
        )

        # 1. Search WITH graph: HiddenCrownJewel is retrieved via multi-hop
        res_with_graph = await dao.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["ds1"],
            query="SourceEntity",
            limit=10,
        )
        names_with_graph = {r["entity"]["canonical_name"] for r in res_with_graph}
        assert "HiddenCrownJewel" in names_with_graph

        # 2. Search WITHOUT graph: HiddenCrownJewel is absent
        dao_no_graph = MemoryDAO(sqlite_engine=sql, vector_engine=None, graph_provider=None)
        res_no_graph = await dao_no_graph.search_v4_memory(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["ds1"],
            query="SourceEntity",
            limit=10,
        )
        names_no_graph = {r["entity"]["canonical_name"] for r in res_no_graph}
        assert "HiddenCrownJewel" not in names_no_graph
    finally:
        await graph.close()
        await sql.close()


@pytest.mark.asyncio
async def test_p9_10_harmful_graph_candidate_detected(tmp_path):
    """10. Candidates with competing statute collision or low confidence are penalized/dropped."""
    sql, graph, dao = await _create_graph_env(tmp_path)
    try:
        seed, _, _ = await _ingest_test_assertion(
            dao, graph, mutation_id="m0", subject_name="LegalSeed", predicate="rules", object_name="Start"
        )
        # Harmonious TBK target
        _, tbk_target, a_tbk = await _ingest_test_assertion(
            dao, graph, mutation_id="m1", subject_name="LegalSeed", predicate="duzenler", object_name="TBK_Target",
            evidence_span="TBK m.117 borçlu temerrüdü şartları", jurisdiction="TBK", confidence=1.0
        )
        # Competing TTK target
        _, ttk_target, a_ttk = await _ingest_test_assertion(
            dao, graph, mutation_id="m2", subject_name="LegalSeed", predicate="duzenler", object_name="TTK_Target",
            evidence_span="TTK m.18 basiretli tacir yükümlülüğü", jurisdiction="TTK", confidence=1.0
        )
        # Low confidence target
        _, weak_target, a_weak = await _ingest_test_assertion(
            dao, graph, mutation_id="m3", subject_name="LegalSeed", predicate="suspect", object_name="WeakTarget",
            confidence=0.1
        )

        hits = await graph.search_v4_graph(
            agent_id="test-agent",
            seed_entity_ids=[seed],
            allowed_entity_ids={seed, tbk_target, ttk_target, weak_target},
            allowed_assertion_ids={a_tbk, a_ttk, a_weak},
            max_hops=1,
            limit=10,
            query="TBK m.117 temerrüt",
        )
        hit_names = [h["entity_name"] for h in hits]
        # WeakTarget (conf=0.1 < 0.15) should be filtered out
        assert "WeakTarget" not in hit_names

        # TBK_Target must outrank TTK_Target due to competing statute penalty
        assert "TBK_Target" in hit_names
        if "TTK_Target" in hit_names:
            assert hit_names.index("TBK_Target") < hit_names.index("TTK_Target")
    finally:
        await graph.close()
        await sql.close()
