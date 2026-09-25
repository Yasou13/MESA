"""Phase 5 regression tests: Assertion Lane Redesign hardening.

Verifies:
1. Natural-language legal questions populate the assertion lane (no full-question substring dependency).
2. Exact predicate matching contributes meaningfully to assertion ranking.
3. Subject and Object/Literal matching.
4. Turkish casing and dotted/dotless-I compatibility.
5. Safe escaping of '%' and '_' wildcards in LIKE queries.
6. Same-article collision disambiguation in assertion lane.
7. Assertion lane is NOT empty for valid epistemic queries.
8. Evidence identity (assertion_id, source_chunk_id, evidence_span) is preserved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def _make_dao(engine: AsyncEngine) -> MemoryDAO:
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        traverse_paths=AsyncMock(return_value=[]),
    )
    return MemoryDAO(engine, vector, graph)


async def _seed_assertion(
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
    confidence: float = 1.0,
):
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-p5",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="revision-p5",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-p5",
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
        "confidence": confidence,
    }
    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assert len(assertions) >= 1
    assertion = assertions[0]

    await dao.project_v4_vector_assertion(mutation=mut, assertion=assertion)
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()
    return assertion


@pytest.mark.asyncio
async def test_phase5_natural_language_query_assertion_lane_not_empty(tmp_path):
    """Verify that a natural language question retrieves candidates via the assertion lane without needing full-sentence substring match."""
    db_path = str(tmp_path / "phase5_nl.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        ass = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-temerrut-nl",
            subject="TBK m.117",
            predicate="hükmü",
            literal_value="Borçlunun temerrüdü ve ihtar şartı",
            evidence_span="Muaccel bir borcun borçlusu alacaklının ihtarıyla temerrüde düşer.",
            raw_log_id=1,
        )

        # Full natural language question - previously failed because whole question was passed to LIKE
        query = (
            "Muaccel bir borcun borçlusu hangi hallerde ve ne şekilde temerrüde düşer?"
        )
        results = await dao.search_v4_memory(
            query=query,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        top = results[0]
        assert top["assertion_id"] == ass["assertion_id"]
        # Must participate in the assertion lane
        assert (
            "assertion" in top["retrieval_provenance"]["origins"]
        ), f"Expected assertion lane participation, got {top['retrieval_provenance']['origins']}"
        assert top["retrieval_provenance"]["lane_ranks"].get("assertion") is not None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_turkish_dotted_i_match_is_admitted_from_evidence_only(tmp_path):
    engine = AsyncEngine(str(tmp_path / "phase5-turkish-evidence-only.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)

    try:
        assertion = await _seed_assertion(
            dao,
            tenant_id="tenant-p5-evidence",
            agent_id="agent-p5-evidence",
            dataset_id="dataset-p5-evidence",
            doc_id="doc-p5-evidence",
            subject="UnrelatedSubject",
            predicate="requires_notice",
            literal_value="Unrelated literal",
            evidence_span="Borçluya İHTAR gönderilmesi zorunludur.",
            raw_log_id=501,
        )

        results = await dao.search_v4_memory(
            tenant_id="tenant-p5-evidence",
            agent_id="agent-p5-evidence",
            dataset_ids=["dataset-p5-evidence"],
            query="ihtar",
        )

        assert assertion["assertion_id"] in {
            result["assertion_id"] for result in results
        }
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_exact_predicate_matching(tmp_path):
    """Verify that exact predicate in query boosts matching assertions in assertion lane."""
    db_path = str(tmp_path / "phase5_pred.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        ass1 = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-yasaklar",
            subject="RekabetHukuku",
            predicate="yasaklar",
            literal_value="Hâkim durumun kötüye kullanılması",
            evidence_span="Belirli teşebbüslerin piyasadaki hâkim durumlarını kötüye kullanmaları yasaktır.",
            raw_log_id=10,
        )

        await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tanimlar",
            subject="RekabetHukuku",
            predicate="tanımlar",
            literal_value="Piyasa kavramı ve ilgili pazar",
            evidence_span="İlgili pazar kavramı coğrafi pazar ve ürün pazarını kapsar.",
            raw_log_id=20,
        )

        # Query targeting 'yasaklar' predicate
        results = await dao.search_v4_memory(
            query="Rekabet hukuku neyi yasaklar?",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass1["assertion_id"]
        assert "assertion" in results[0]["retrieval_provenance"]["origins"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_like_wildcard_escaping(tmp_path):
    """Verify that '%' and '_' characters in queries do not break SQL or cause wildcard explosion."""
    db_path = str(tmp_path / "phase5_escape.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        ass = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-faiz",
            subject="Mevzuat",
            predicate="oran_belirler",
            literal_value="Gecikme faizi oranı %50 olarak uygulanır",
            evidence_span="Yıllık temerrüt faizi oranı %50 sınırını aşamaz.",
            raw_log_id=30,
        )

        # Query with % literal
        results = await dao.search_v4_memory(
            query="faizi oranı %50",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass["assertion_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_evidence_identity_preservation(tmp_path):
    """Verify that assertion lane preserves exact assertion_id, source_chunk_id, and evidence_span."""
    db_path = str(tmp_path / "phase5_identity.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        ass = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-spec",
            subject="CezaKanunu",
            predicate="yaptırım_öngörür",
            literal_value="Adli para cezası",
            evidence_span="Kişiye beş günden yedi yüz otuz güne kadar adli para cezası verilir.",
            raw_log_id=40,
        )

        results = await dao.search_v4_memory(
            query="Adli para cezası yaptırımı",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        top = results[0]
        assert top["assertion_id"] == ass["assertion_id"]
        assert top["source_chunk_id"] == "chunk-doc-spec"
        assert top["evidence_span"] == ass["evidence_span"]
        assert len(top["matched_assertions"]) >= 1
        assert top["matched_assertions"][0]["assertion_id"] == ass["assertion_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_turkish_dotted_dotless_i_matching(tmp_path):
    """Verify that Turkish casing differences (İHTAR vs ihtar, IŞIK vs ışık) match accurately in assertion lane."""
    db_path = str(tmp_path / "phase5_turkish_i.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        ass = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-ihtar",
            subject="BorçlarHukuku",
            predicate="şart_koşar",
            literal_value="İhtar ve ek süre",
            evidence_span="Temerrüt için alacaklının yazılı ihtarda bulunması gerekir.",
            raw_log_id=50,
        )

        # Query with lowercase 'ihtar' when assertion has uppercase 'İhtar' and vice versa
        results = await dao.search_v4_memory(
            query="alacaklının İHTARDA bulunma şartı",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass["assertion_id"]
        assert "assertion" in results[0]["retrieval_provenance"]["origins"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase5_same_article_collision_disambiguation(tmp_path):
    """Verify that TBK 117 query prioritizes TBK 117 assertion and penalizes TMK 117 assertion in assertion lane."""
    db_path = str(tmp_path / "phase5_collision.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p5"
    agent_id = "agent-p5"
    dataset_id = "dataset-p5"

    try:
        # Target: TBK 117 (Borçlar Kanunu - Temerrüt)
        ass_tbk = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tbk-117",
            subject="TBK m.117",
            predicate="hükmü",
            literal_value="Borçlunun temerrüdü ve ihtar",
            evidence_span="Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer.",
            raw_log_id=60,
        )

        # Distractor: TMK 117 (Medeni Kanun - Mirasçılık / Boşanma / Ayrılık)
        ass_tmk = await _seed_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tmk-117",
            subject="TMK m.117",
            predicate="hükmü",
            literal_value="Evlenmenin butlanı ve hak düşürücü süreler",
            evidence_span="Batıl bir evlilik ancak hâkimin kararıyla sona erer.",
            raw_log_id=70,
        )

        # Query targeting TBK 117
        results = await dao.search_v4_memory(
            query="TBK m.117 uyarınca borçlunun temerrüde düşmesi",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass_tbk["assertion_id"]
        # TMK 117 must not rank above TBK 117
        if len(results) > 1 and results[1]["assertion_id"] == ass_tmk["assertion_id"]:
            assert results[0]["rrf_score"] > results[1]["rrf_score"]
    finally:
        await engine.close()
