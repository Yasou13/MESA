"""Phase 10 regression tests: Evidence-First ContextBuilder.

Verifies all 14 Phase 10 requirements:
1. large entity / many provenance
2. correct evidence crowded out değil
3. subject/object names
4. direction
5. no opaque UUID
6. exact token budget
7. raw/debug budget bypass yok
8. cold tokenizer
9. REL multi-evidence context
10. Phase 1 evidence identity survives
11. Phase 7 scope preserved
12. Phase 9 relation rendered
13. Phase 8 typed literal/entity rendered
14. evidence text/span fixed 200-char truncation does not silently destroy required legal meaning
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from mesa_memory.context_builder import ContextBuilder, _count_tokens
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _create_test_env(tmp_path, *, agent_id: str = "test-agent"):
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


# 1. large entity / many provenance & 2. correct evidence not crowded out
@pytest.mark.asyncio
async def test_p10_large_entity_does_not_crowd_out_other_entities():
    """Verify that a large entity with many facts does not crowd out smaller entities."""
    # Entity A has 15 facts, Entity B has 1 high-priority fact
    entity_a_facts = [
        {
            "predicate": f"FACT_{i}",
            "literal_value": f"Detailed verbose fact statement {i} for entity A",
            "confidence": 0.9 - 0.02 * i,
        }
        for i in range(15)
    ]
    entity_b_facts = [
        {
            "predicate": "ESSENTIAL_RULE",
            "literal_value": "Critical rule statement for entity B",
            "confidence": 1.0,
        }
    ]
    mock_memories = [
        {
            "entity": {"canonical_name": "Entity_A"},
            "provenance": entity_a_facts,
            "rrf_score": 0.05,
        },
        {
            "entity": {"canonical_name": "Entity_B"},
            "provenance": entity_b_facts,
            "rrf_score": 0.04,
        },
    ]

    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = mock_memories

    cb = ContextBuilder(mock_dao)
    ctx = await cb.build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="test",
        token_budget=150,  # Constrained budget
    )

    formatted = ctx["formatted_context"]
    assert ctx["actual_token_count"] <= 150
    # Entity B must NOT be crowded out by Entity A
    assert "Entity_B" in formatted
    assert "Critical rule statement for entity B" in formatted
    # Entity A must still be present with its top facts
    assert "Entity_A" in formatted


# 3. subject/object names & 4. direction & 5. no opaque UUID
@pytest.mark.asyncio
async def test_p10_subject_object_direction_no_opaque_uuid():
    """Verify clean canonical names, direction, and no opaque UUIDs leaked into context."""
    opaque_uuid = "e_7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c"
    mock_memories = [
        {
            "entity": {"canonical_name": "TBK_Madde_117"},
            "provenance": [
                {
                    "predicate": "sonucudur",
                    "object_name": "Temerrut_Ihtari",
                    "object_entity_id": opaque_uuid,
                    "direction": "forward",
                    "evidence_span": "TBK m.117 gereğince temerrüt ihtarla gerçekleşir.",
                },
                {
                    "predicate": "istisnasidir",
                    "object_name": None,
                    "object_entity_id": opaque_uuid,  # Only opaque UUID available!
                    "direction": "reverse",
                },
            ],
            "rrf_score": 0.08,
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
        query="TBK 117",
        token_budget=500,
    )

    formatted = ctx["formatted_context"]
    assert "TBK_Madde_117" in formatted
    assert "Temerrut_Ihtari" in formatted
    # Opaque UUID must NEVER be present as model-visible value
    assert opaque_uuid not in formatted

    # Parse JSON to verify direction
    for line in formatted.splitlines():
        if line.startswith("{") and line.endswith("}"):
            parsed = json.loads(line)
            facts = parsed.get("facts", [])
            assert len(facts) >= 1
            assert facts[0]["direction"] == "forward"
            assert facts[0]["value"] == "Temerrut_Ihtari"


# 6. exact token budget & 7. no raw/debug budget bypass
@pytest.mark.asyncio
async def test_p10_exact_token_budget_and_no_raw_bypass():
    """Verify hard token budget is met and canonical_memories does not leak unbudgeted items."""
    huge_memories = [
        {
            "entity": {"canonical_name": f"Heavy_Entity_{i}"},
            "provenance": [
                {
                    "predicate": "DESC",
                    "literal_value": f"Extremely long text payload description block for entity {i} "
                    * 5,
                }
            ],
            "rrf_score": 0.01 * (10 - i),
        }
        for i in range(10)
    ]
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = huge_memories

    cb = ContextBuilder(mock_dao)
    budget = 100
    ctx = await cb.build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="Heavy",
        token_budget=budget,
    )

    # 1. Exact budget adherence
    assert ctx["actual_token_count"] <= budget
    assert ctx["estimated_token_count"] <= budget

    # 2. No raw context bypass: canonical_memories must NOT contain all 10 unbudgeted items
    assert len(ctx["canonical_memories"]) < 10
    # Model visible memories strictly matches formatted context
    for m in ctx["canonical_memories"]:
        assert m["entity"]["canonical_name"] in ctx["formatted_context"]

    # 3. Raw search is isolated under _debug_raw_retrieval
    assert len(ctx["_debug_raw_retrieval"]) == 10


# 8. cold tokenizer offline deterministic
def test_p10_cold_tokenizer_offline_deterministic():
    """Verify tokenizer executes deterministically on Turkish legal text."""
    sample_text = (
        "Borçlunun temerrüdü: Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer. "
        "Borcun ifa edileceği gün, birlikte belirlenmiş veya sözleşmede saklı tutulan bir hakka "
        "dayanılarak taraflardan biri tarafından usulüne uygun olarak bildirilmişse, bu günün "
        "geçmesiyle borçlu temerrüde düşmüş olur."
    )
    t1 = _count_tokens(sample_text)
    t2 = _count_tokens(sample_text)
    assert t1 == t2
    assert t1 > 0


# 14. evidence text/span fixed 200-char truncation does not destroy required legal meaning
@pytest.mark.asyncio
async def test_p10_evidence_span_preserves_legal_meaning_beyond_200_chars():
    """Verify evidence span longer than 200 characters is NOT truncated at 200 chars."""
    legal_passage = (
        "Borçlunun temerrüdü için kural olarak alacaklının ihtarı şarttır. "
        "Ancak taraflar sözleşmede belirli bir vade kararlaştırmışlarsa, bu vadenin dolmasıyla "
        "birlikte ayrıca bir ihtara gerek kalmaksızın borçlu kendiliğinden temerrüde düşer. "
        "Haksız fiilde ise fiilin işlendiği tarihten itibaren temerrüt faizi işlemeye başlar."
    )
    assert len(legal_passage) > 300  # Well over 200 characters!
    mock_memories = [
        {
            "entity": {"canonical_name": "Temerrut_Hukuku"},
            "provenance": [
                {
                    "predicate": "hukum",
                    "literal_value": "Vade ve İhtar Hükümleri",
                    "evidence_span": legal_passage,
                    "source_ref": "TBK m.117/2",
                }
            ],
            "rrf_score": 0.1,
        }
    ]
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = mock_memories
    ctx = await ContextBuilder(mock_dao).build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="temerrüt",
        token_budget=1000,
    )
    assert (
        "Haksız fiilde ise fiilin işlendiği tarihten itibaren"
        in ctx["formatted_context"]
    )


@pytest.mark.asyncio
async def test_p10_duplicate_predicates_do_not_restore_trimmed_evidence():
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = [
        {
            "entity": {"canonical_name": "BudgetedEntity"},
            "provenance": [
                {
                    "assertion_id": "kept",
                    "predicate": "SAME",
                    "literal_value": "short high-ranked fact",
                },
                {
                    "assertion_id": "trimmed",
                    "predicate": "SAME",
                    "literal_value": "oversized " * 400,
                },
            ],
        }
    ]

    ctx = await ContextBuilder(mock_dao).build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="same",
        token_budget=160,
    )

    assert "short high-ranked fact" in ctx["formatted_context"]
    assert "oversized" not in ctx["formatted_context"]
    assert len(ctx["canonical_memories"][0]["provenance"]) == 1


@pytest.mark.asyncio
async def test_p10_legal_tail_after_legacy_2000_boundary_is_preserved():
    marker = "REQUIRED_TAIL_CONDITION"
    evidence = "A" * 2050 + marker
    mock_dao = AsyncMock()
    mock_dao.get_recent_logs.return_value = []
    mock_dao.search_v4_memory.return_value = [
        {
            "entity": {"canonical_name": "LongEvidence"},
            "provenance": [
                {
                    "predicate": "RULE",
                    "literal_value": "value",
                    "evidence_span": evidence,
                }
            ],
        }
    ]

    ctx = await ContextBuilder(mock_dao).build_context(
        tenant_id="t1",
        agent_id="a1",
        dataset_ids=["ds1"],
        query="rule",
        token_budget=5000,
    )

    assert marker in ctx["formatted_context"]
    assert marker in ctx["canonical_memories"][0]["provenance"][0]["evidence_span"]


# End-to-end integrated tests: 9, 10, 11, 12, 13
@pytest.mark.asyncio
async def test_p10_integrated_retrieval_to_context_builder(tmp_path):
    """Integrated test: MemoryDAO search_v4_memory -> ContextBuilder end-to-end flow.

    Verifies:
    - 9. REL multi-evidence context
    - 10. Phase 1 evidence identity survives (chunk_id, document_id)
    - 11. Phase 7 scope preserved (superseded/expired excluded)
    - 12. Phase 9 relation rendered
    - 13. Phase 8 typed literal/entity rendered
    """
    sql, graph, dao = await _create_test_env(tmp_path)
    try:
        # 1. Active Assertion with typed literal and evidence provenance (Phase 1, 8, 9)
        s_entity = await dao.resolve_v4_entity(
            tenant_id="test-tenant", canonical_name="TBK_117"
        )
        o_entity = await dao.resolve_v4_entity(
            tenant_id="test-tenant", canonical_name="Temerrut_Sartlari"
        )
        s_id = s_entity["entity_id"]
        o_id = o_entity["entity_id"]

        async with dao._sql.transaction() as db:
            ds_id = await dao._catalog.resolve_id_in_tx(
                db, tenant_id="test-tenant", kind="dataset", external_id="ds1"
            )
            doc_id = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="document",
                external_id="doc_tbk",
                create=True,
            )
            rev_id = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="revision",
                external_id="rev_1",
                create=True,
            )
            chk_id = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="chunk",
                external_id="chk_001",
                create=True,
            )
            doc_old = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="document",
                external_id="doc_ebk",
                create=True,
            )
            rev_old = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="revision",
                external_id="rev_old",
                create=True,
            )
            chk_old = await dao._catalog.resolve_id_in_tx(
                db,
                tenant_id="test-tenant",
                kind="chunk",
                external_id="chk_old",
                create=True,
            )
            await db.execute(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
                "VALUES ('pipe_1', 'test-tenant', 'sess_1', 'test-agent', 'COMPLETED')",
            )
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
                "VALUES ('m1', 'c1', 'sess_1', 'test-agent', 'test-tenant', 'TBK m.117 temerrüt şartları', 'COMMITTED')",
            )
            for eid in (s_id, o_id):
                reg_id = f"reg_{eid}"
                await db.execute(
                    "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                    "VALUES (?, 'test-tenant', 'test-agent', 'canonical', 'ENTITY', ?, 'ACTIVE')",
                    (reg_id, eid),
                )
                await db.execute(
                    "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                    "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                    (f"src_{eid}", reg_id, ds_id),
                )

            a_id = f"ast_active_{s_id}"
            await db.execute(
                "INSERT INTO v4_assertions ("
                "assertion_id, tenant_id, dataset_id, subject_id, predicate, object_entity_id, "
                "literal_value, source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id, "
                "evidence_span, jurisdiction, valid_from, valid_to"
                ") VALUES (?, 'test-tenant', ?, ?, 'hukuki_sonucudur', ?, NULL, "
                "'TBK m.117', ?, ?, ?, 1.0, 'ACTIVE', 'm1', 'pipe_1', "
                "'Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer.', 'TBK', '2024-01-01', '2024-12-31')",
                (a_id, ds_id, s_id, o_id, doc_id, rev_id, chk_id),
            )
            reg_aid = f"reg_vec_{a_id}"
            await db.execute(
                "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, 'test-tenant', 'test-agent', 'canonical', 'ASSERTION_VECTOR', ?, 'ACTIVE')",
                (reg_aid, a_id),
            )
            await db.execute(
                "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, 'm1', ?, 'ACTIVE')",
                (f"src_vec_{a_id}", reg_aid, ds_id),
            )

            # 2. Superseded Assertion (Phase 7: must be filtered out when valid_at is not given)
            a_old = f"ast_superseded_{s_id}"
            await db.execute(
                "INSERT INTO v4_assertions ("
                "assertion_id, tenant_id, dataset_id, subject_id, predicate, literal_value, "
                "source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id"
                ") VALUES (?, 'test-tenant', ?, ?, 'mulga_hukum', 'Eski BK 101 hukmu', 'Eski BK', ?, ?, ?, 1.0, 'SUPERSEDED', 'm1', 'pipe_1')",
                (a_old, ds_id, s_id, doc_old, rev_old, chk_old),
            )
            await db.commit()

        # Ingest active into graph
        await graph.insert_node(s_id, "TBK_117", agent_id="test-agent")
        await graph.insert_node(o_id, "Temerrut_Sartlari", agent_id="test-agent")
        await graph.insert_assertion(
            assertion_id=a_id,
            agent_id="test-agent",
            subject_id=s_id,
            predicate="hukuki_sonucudur",
            object_id=o_id,
            evidence_span="Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer.",
            jurisdiction="TBK",
            status="ACTIVE",
            mutation_id="m1",
        )

        cb = ContextBuilder(dao)
        ctx = await cb.build_context(
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_ids=["ds1"],
            query="TBK m.117 temerrüt",
            token_budget=1000,
        )

        formatted = ctx["formatted_context"]

        # Phase 1: chunk_id and document_id survive
        assert "chk_001" in formatted
        assert "doc_tbk" in formatted

        # Phase 7: Superseded assertion is excluded!
        assert "Eski BK 101 hukmu" not in formatted
        assert "chk_old" not in formatted

        # Phase 8 & 9: Relation and direction rendered
        assert "hukuki_sonucudur" in formatted
        assert "direction" in formatted

        # Model visible contract
        assert len(ctx["canonical_memories"]) >= 1
    finally:
        await graph.close()
        await sql.close()
