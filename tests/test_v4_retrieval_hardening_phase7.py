"""Phase 7 regression tests: Temporal / Jurisdiction / Status Filtering Before Rank Contribution.

Verifies:
1. Superseded vs active assertion for same entity: superseded does not contribute rank in live search.
2. Temporal filtering: old / past versions excluded when query valid_at is current.
3. Temporal filtering: future versions excluded when query valid_at is current.
4. Wrong jurisdiction: assertion with jurisdiction='DE' cannot contribute rank to query for jurisdiction='TR'.
5. Wrong tenant isolation.
6. Wrong dataset isolation.
7. Wrong agent isolation.
8. Stale vector physically present in vector store but pre-filtered before rank contribution.
9. Stale lexical row does not contribute score or rank.
10. Graph seed entities are strictly verified in-scope before graph traversal.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def _make_dao(
    engine: AsyncEngine, mock_vector_rows: list[dict] | None = None
) -> MemoryDAO:
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=mock_vector_rows or []),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        traverse_paths=AsyncMock(return_value=[]),
    )
    return MemoryDAO(engine, vector, graph)


async def _seed_versioned_assertion(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    doc_id: str,
    subject: str,
    predicate: str,
    literal_value: str,
    evidence_span: str,
    raw_log_id: int,
    status: str = "ACTIVE",
    jurisdiction: str = "TR",
    valid_from: str = "",
    valid_to: str = "",
):
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-p7",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=f"rev-{doc_id}",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-p7",
        content_payload=f"{subject} {predicate} {literal_value}. {evidence_span}",
        embedding_provider="test",
        embedding_model="catalog-contract",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:catalog-contract:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(cand, raw_log_id=raw_log_id)
    mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
    assert mut is not None

    await dao.project_v4_sql_entity(mutation=mut, entity_name=subject)
    triplet = {
        "head": subject,
        "relation": predicate,
        "literal_value": literal_value,
        "evidence_span": evidence_span,
        "confidence": 1.0,
        "metadata": {
            "jurisdiction": jurisdiction,
            "valid_from": valid_from,
            "valid_to": valid_to,
        },
    }
    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assertion = assertions[0]

    # Update assertion with exact status, jurisdiction, and validity dates
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE v4_assertions SET status = ?, jurisdiction = ?, valid_from = ?, valid_to = ? "
            "WHERE assertion_id = ?",
            (status, jurisdiction, valid_from, valid_to, assertion["assertion_id"]),
        )
        await db.commit()

    await dao.project_v4_vector_assertion(mutation=mut, assertion=assertion)
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()
    return assertion


async def _seed_entity_only(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    subject: str,
    raw_log_id: int,
) -> None:
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-p7",
        dataset_id=dataset_id,
        document_id=f"doc-entity-{raw_log_id}",
        revision_id=f"rev-entity-{raw_log_id}",
        chunk_id=f"chunk-entity-{raw_log_id}",
        source_ref=f"source-entity-{raw_log_id}",
        agent_id=agent_id,
        session_id="session-p7",
        content_payload=subject,
        embedding_provider="test",
        embedding_model="catalog-contract",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:catalog-contract:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(candidate, raw_log_id=raw_log_id)
    mutation = await dao.get_projection_mutation(str(candidate["mutation_id"]))
    assert mutation is not None
    await dao.project_v4_sql_entity(mutation=mutation, entity_name=subject)
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mutation["mutation_id"],),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_p7_shared_entity_fallback_cannot_leak_another_agents_assertion(tmp_path):
    engine = AsyncEngine(str(tmp_path / "p7-shared-entity-agent.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)

    try:
        await _seed_entity_only(
            dao,
            tenant_id="tenant-p7-shared",
            agent_id="agent-a",
            dataset_id="dataset-p7-shared",
            subject="SharedEntity",
            raw_log_id=7001,
        )
        await _seed_versioned_assertion(
            dao,
            tenant_id="tenant-p7-shared",
            agent_id="agent-b",
            dataset_id="dataset-p7-shared",
            doc_id="document-agent-b",
            subject="SharedEntity",
            predicate="belongs_to_agent_b",
            literal_value="private fact",
            evidence_span="Only agent B may retrieve this evidence.",
            raw_log_id=7002,
        )

        results = await dao.search_v4_memory(
            tenant_id="tenant-p7-shared",
            agent_id="agent-a",
            dataset_ids=["dataset-p7-shared"],
            query="SharedEntity",
        )

        assert results == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_public_top_level_document_id_matches_translated_provenance(tmp_path):
    engine = AsyncEngine(str(tmp_path / "p7-public-document-id.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)

    try:
        await _seed_versioned_assertion(
            dao,
            tenant_id="tenant-p7-public",
            agent_id="agent-p7-public",
            dataset_id="dataset-p7-public",
            doc_id="document-public",
            subject="PublicEntity",
            predicate="has_public_id",
            literal_value="public fact",
            evidence_span="Public evidence.",
            raw_log_id=7003,
        )
        results = await dao.search_v4_memory(
            tenant_id="tenant-p7-public",
            agent_id="agent-p7-public",
            dataset_ids=["dataset-p7-public"],
            query="PublicEntity",
        )

        assert results
        assert results[0]["provenance"][0]["document_id"] == "document-public"
        assert results[0]["document_id"] == "document-public"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_superseded_and_active_assertion_same_entity(tmp_path):
    """Verify that superseded assertion does not contribute to rank in active live query, while active does."""
    db_path = str(tmp_path / "p7_status.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"

    try:
        # 1. Superseded assertion (old legal rule)
        ass_old = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-old",
            subject="VergiOrani",
            predicate="orani_belirler",
            literal_value="Eski KDV oranı %18",
            evidence_span="Genel KDV oranı %18 olarak uygulanır.",
            raw_log_id=1,
            status="SUPERSEDED",
        )

        # 2. Active assertion (current legal rule)
        ass_active = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-new",
            subject="VergiOrani",
            predicate="orani_belirler",
            literal_value="Yeni KDV oranı %20",
            evidence_span="Genel KDV oranı %20 olarak değiştirilmiştir.",
            raw_log_id=2,
            status="ACTIVE",
        )

        # Query targeting KDV rate without historical flag
        results = await dao.search_v4_memory(
            query="Genel KDV oranı kaçtır",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        # Only active assertion must be retrieved
        retrieved_aids = [r["assertion_id"] for r in results]
        assert ass_active["assertion_id"] in retrieved_aids
        assert ass_old["assertion_id"] not in retrieved_aids
        assert results[0]["assertion_id"] == ass_active["assertion_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_temporal_filtering_past_and_future_windows(tmp_path):
    """Verify that assertions outside the temporal window (past expired or future not yet in force) are excluded."""
    db_path = str(tmp_path / "p7_temporal.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"

    try:
        # Past expired: valid 2018 to 2021
        ass_past = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-past",
            subject="AsgariUcret",
            predicate="miktari",
            literal_value="2825 TL",
            evidence_span="2021 yılı net asgari ücreti 2825 TL olarak belirlenmiştir.",
            raw_log_id=10,
            valid_from="2021-01-01",
            valid_to="2021-12-31",
        )

        # Current: valid 2024 to 2025
        ass_current = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-current",
            subject="AsgariUcret",
            predicate="miktari",
            literal_value="17002 TL",
            evidence_span="2024 yılı net asgari ücreti 17002 TL olarak belirlenmiştir.",
            raw_log_id=11,
            valid_from="2024-01-01",
            valid_to="2024-12-31",
        )

        # Future: valid 2030+
        ass_future = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-future",
            subject="AsgariUcret",
            predicate="miktari",
            literal_value="50000 TL",
            evidence_span="2030 yılı tahmini asgari ücreti 50000 TL.",
            raw_log_id=12,
            valid_from="2030-01-01",
            valid_to="2030-12-31",
        )

        # Query for 2024 validity
        results_2024 = await dao.search_v4_memory(
            query="Net asgari ücret miktarı",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            valid_at="2024-06-01",
        )

        retrieved_ids = [r["assertion_id"] for r in results_2024]
        assert ass_current["assertion_id"] in retrieved_ids
        assert ass_past["assertion_id"] not in retrieved_ids
        assert ass_future["assertion_id"] not in retrieved_ids
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_wrong_jurisdiction_filtering(tmp_path):
    """Verify that an assertion from another jurisdiction cannot contribute rank or return in results."""
    db_path = str(tmp_path / "p7_jurisdiction.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"

    try:
        # German jurisdiction
        ass_de = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-de",
            subject="Verzugszinsen",
            predicate="regelt",
            literal_value="BGB 288 Verzugszinsen",
            evidence_span="Der Verzugszinssatz beträgt für das Jahr fünf Prozentpunkte über dem Basiszinssatz.",
            raw_log_id=20,
            jurisdiction="DE",
        )

        # Turkish jurisdiction
        ass_tr = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tr",
            subject="TemerrutFaizi",
            predicate="hükmü",
            literal_value="TBK 120 Temerrüt Faizi",
            evidence_span="Akdî faiz oranı kararlaştırılmamışsa, yıllık temerrüt faizi oranı kanunî faiz oranıdır.",
            raw_log_id=21,
            jurisdiction="TR",
        )

        # Query specifying jurisdiction='TR'
        results_tr = await dao.search_v4_memory(
            query="Temerrüt faizi oranı ve şartları",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            jurisdiction="TR",
        )

        assert len(results_tr) >= 1
        retrieved_ids = [r["assertion_id"] for r in results_tr]
        assert ass_tr["assertion_id"] in retrieved_ids
        assert ass_de["assertion_id"] not in retrieved_ids
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_tenant_dataset_and_agent_isolation(tmp_path):
    """Verify strict tenant, dataset, and agent isolation."""
    db_path = str(tmp_path / "p7_isolation.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)

    try:
        # Tenant A data
        await _seed_versioned_assertion(
            dao,
            tenant_id="tenant-A",
            agent_id="agent-1",
            dataset_id="dataset-alpha",
            doc_id="doc-iso-1",
            subject="SirrEntity",
            predicate="tanımlar",
            literal_value="Gizli ticari sırlar",
            evidence_span="Bu bilgi yalnızca Tenant A içindir.",
            raw_log_id=30,
        )

        # Seed Tenant B's own dataset
        await _seed_versioned_assertion(
            dao,
            tenant_id="tenant-B",
            agent_id="agent-1",
            dataset_id="dataset-b-own",
            doc_id="doc-b-1",
            subject="OtherEntity",
            predicate="hükmü",
            literal_value="Tenant B bilgisi",
            evidence_span="Tenant B içeriği.",
            raw_log_id=31,
        )

        # 1. Wrong tenant querying with its own dataset cannot see Tenant A's private secrets
        res_tenant_b = await dao.search_v4_memory(
            query="Gizli ticari sırlar",
            tenant_id="tenant-B",
            agent_id="agent-1",
            dataset_ids=["dataset-b-own"],
            limit=5,
        )
        assert len(res_tenant_b) == 0

        # Cross-tenant dataset identifier lookup fails closed
        from mesa_storage.repositories.catalog import CatalogIdentityNotFoundError

        with pytest.raises(CatalogIdentityNotFoundError):
            await dao.search_v4_memory(
                query="Gizli ticari sırlar",
                tenant_id="tenant-B",
                agent_id="agent-1",
                dataset_ids=["dataset-alpha"],
                limit=5,
            )

        # Seed dataset-beta in Tenant A
        await _seed_versioned_assertion(
            dao,
            tenant_id="tenant-A",
            agent_id="agent-1",
            dataset_id="dataset-beta",
            doc_id="doc-beta-1",
            subject="BetaEntity",
            predicate="hükmü",
            literal_value="Beta içeriği",
            evidence_span="Beta bilgisi.",
            raw_log_id=32,
        )

        # 2. Wrong dataset: dataset-beta does not see dataset-alpha's data
        res_wrong_dataset = await dao.search_v4_memory(
            query="Gizli ticari sırlar",
            tenant_id="tenant-A",
            agent_id="agent-1",
            dataset_ids=["dataset-beta"],
            limit=5,
        )
        assert len(res_wrong_dataset) == 0

        # 3. Wrong agent: agent-2 in tenant-A does not see agent-1's data
        res_wrong_agent = await dao.search_v4_memory(
            query="Gizli ticari sırlar",
            tenant_id="tenant-A",
            agent_id="agent-2",
            dataset_ids=["dataset-alpha"],
            limit=5,
        )
        assert len(res_wrong_agent) == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_stale_vector_physically_exists_but_excluded_from_rank(tmp_path):
    """Verify that even if LanceDB vector index returns a superseded assertion node_id, it is pre-filtered and never enters vector_lane."""
    db_path = str(tmp_path / "p7_stale_vector.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"

    # Seed an active assertion and a superseded assertion
    dummy_dao = _make_dao(engine)
    ass_stale = await _seed_versioned_assertion(
        dummy_dao,
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_id=dataset_id,
        doc_id="doc-stale-vec",
        subject="CezaYasalari",
        predicate="hükmü",
        literal_value="Eski ceza miktarı",
        evidence_span="Eski yasa hükmünce hapis cezası uygulanır.",
        raw_log_id=40,
        status="SUPERSEDED",
    )

    ass_live = await _seed_versioned_assertion(
        dummy_dao,
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_id=dataset_id,
        doc_id="doc-live-vec",
        subject="CezaYasalari",
        predicate="hükmü",
        literal_value="Yeni ceza miktarı",
        evidence_span="Yeni yasa hükmünce adli para cezası uygulanır.",
        raw_log_id=41,
        status="ACTIVE",
    )

    # Mock vector engine returning the stale assertion as #1 distance hit
    stale_aid = ass_stale["assertion_id"]
    live_aid = ass_live["assertion_id"]
    mock_vector_hits = [
        {"node_id": stale_aid, "_distance": 0.01},
        {"node_id": live_aid, "_distance": 0.05},
    ]

    dao_with_mock_vec = _make_dao(engine, mock_vector_rows=mock_vector_hits)

    results = await dao_with_mock_vec.search_v4_memory(
        query="Ceza yasası hapis veya adli para cezası",
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        limit=5,
    )

    assert len(results) >= 1
    # The stale assertion MUST NOT be in results
    result_aids = [r["assertion_id"] for r in results]
    assert stale_aid not in result_aids
    assert live_aid in result_aids
    # Stale assertion must NOT be credited in vector lane
    for r in results:
        assert r["assertion_id"] != stale_aid
    await engine.close()


@pytest.mark.asyncio
async def test_p7_stale_lexical_row_excluded_from_rank(tmp_path):
    """Verify that a superseded assertion or wrong jurisdiction text in lexical search cannot enter lexical lane."""
    db_path = str(tmp_path / "p7_stale_lex.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"
    dao = _make_dao(engine)

    try:
        # Superseded assertion matching exact query keywords
        ass_super = await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-super-lex",
            subject="VergiHukuku",
            predicate="hükmü",
            literal_value="Yürürlükten kaldırılan maktu harç tarifesi",
            evidence_span="Maktu harç tarifesi yürürlükten tamamen kaldırılmıştır.",
            raw_log_id=50,
            status="SUPERSEDED",
        )

        # Active assertion with partial match
        await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-act-lex",
            subject="VergiHukuku",
            predicate="hükmü",
            literal_value="Güncel harçlar ve vergi tarifesi",
            evidence_span="Güncel harç tarifesi nispi olarak hesaplanır.",
            raw_log_id=51,
            status="ACTIVE",
        )

        results = await dao.search_v4_memory(
            query="Maktu harç tarifesi",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        # Superseded row MUST NOT be returned in active query
        retrieved = [r["assertion_id"] for r in results]
        assert ass_super["assertion_id"] not in retrieved
        if results:
            assert "SUPERSEDED" not in [r.get("status") for r in results]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_p7_graph_seeds_strictly_in_scope(tmp_path):
    """Verify that graph traversal seeds are strictly drawn from in-scope entities only."""
    db_path = str(tmp_path / "p7_graph_seeds.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    tenant_id = "tenant-p7"
    agent_id = "agent-p7"
    dataset_id = "dataset-p7"
    dao = _make_dao(engine)

    try:
        # Entity with only DE jurisdiction assertion
        await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-de-seed",
            subject="GermanEntity",
            predicate="hükmü",
            literal_value="Deutsches Recht",
            evidence_span="Deutsches Recht gilt hier.",
            raw_log_id=60,
            jurisdiction="DE",
        )

        # Entity with TR jurisdiction assertion
        await _seed_versioned_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tr-seed",
            subject="TurkishEntity",
            predicate="hükmü",
            literal_value="Türk Hukuku",
            evidence_span="Türk hukuku kuralları geçerlidir.",
            raw_log_id=61,
            jurisdiction="TR",
        )

        # Query for jurisdiction='TR'
        results = await dao.search_v4_memory(
            query="hukuku kuralları ve geçerlilik",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
            jurisdiction="TR",
        )

        # GermanEntity must NOT be in results
        for r in results:
            if "entity" in r and r["entity"]:
                assert r["entity"].get("canonical_name") != "GermanEntity"
                assert r["entity"].get("entity_id") != "GermanEntity"
            assert "DE" not in str(r.get("evidence_span", ""))
    finally:
        await engine.close()
