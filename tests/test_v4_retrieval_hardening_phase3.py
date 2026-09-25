"""Phase 3 regression tests: Legal Identity + Türkçe Normalization hardening.

Verifies:
1. Legal citation resolution across diverse formats:
   - "TBK m.117"
   - "Türk Borçlar Kanunu 117"
   - "TBK 117. madde"
   - "TBK 117"
2. Same-article different-statute disambiguation (TBK 117 vs TMK 117 vs TCK 117; CMK 141 vs TCK 141 vs Anayasa 141).
3. Proximal association prevents Cartesian product bug ("TBK 117 ve TMK 50" does not link 50 to TBK or 117 to TMK).
4. Turkish-I normalization matrix (İ, I, i, ı, NFD, NFC) for "İş Kanunu", "iş kanunu", "İŞ KANUNU".
5. Statute coverage: TBK, TMK, TCK, CMK, HMK, TTK, İYUK, KVKK, İş Kanunu, Anayasa.
6. Downstream candidate alignment in search_v4_memory (statute matching boost, collision prevention).
"""

from __future__ import annotations

import unicodedata
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.retrieval.legal_resolver import LegalEntityResolver
from mesa_storage.dao import MemoryDAO, _normalize_identity_text
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def test_phase3_turkish_i_normalization_matrix():
    """Verify Turkish-I matrix behaves deterministically and maps variations consistently."""
    variations = [
        "İş Kanunu",
        "iş kanunu",
        "İŞ KANUNU",
        unicodedata.normalize("NFD", "İŞ KANUNU"),
        unicodedata.normalize("NFC", "İŞ KANUNU"),
    ]
    normalized_set = {_normalize_identity_text(v) for v in variations}
    assert (
        len(normalized_set) == 1
    ), f"Expected 1 canonical normalized form, got {normalized_set}"
    assert "iş kanunu" in normalized_set

    # Verify dotted İ and small i map to the same letter
    assert _normalize_identity_text("İ") == _normalize_identity_text("i")
    # Verify dotless I and small ı map to the same letter
    assert _normalize_identity_text("I") == _normalize_identity_text("ı")


def test_phase3_legal_resolver_citation_formats():
    """Verify LegalEntityResolver resolves all standard citation formats."""
    resolver = LegalEntityResolver()

    # 1. TBK m.117
    e1 = resolver.extract_entities("TBK m.117 uyarınca temerrüt")
    assert any(
        "TBK" in x and "117" in x for x in e1
    ), f"Failed to resolve 'TBK m.117': {e1}"

    # 2. Türk Borçlar Kanunu 117
    e2 = resolver.extract_entities(
        "Türk Borçlar Kanunu 117 gereğince borçlunun temerrüdü"
    )
    assert any(
        "TBK" in x and "117" in x for x in e2
    ), f"Failed to resolve 'Türk Borçlar Kanunu 117': {e2}"

    # 3. TBK 117. madde
    e3 = resolver.extract_entities("TBK 117. madde kapsamında fesih")
    assert any(
        "TBK" in x and "117" in x for x in e3
    ), f"Failed to resolve 'TBK 117. madde': {e3}"

    # 4. TBK 117
    e4 = resolver.extract_entities("TBK 117 ihtar şartı")
    assert any(
        "TBK" in x and "117" in x for x in e4
    ), f"Failed to resolve 'TBK 117': {e4}"

    # 5. Reverse: 117. maddesi TBK
    e5 = resolver.extract_entities("117. maddesi uyarınca TBK kapsamında")
    assert any(
        "TBK" in x and "117" in x for x in e5
    ), f"Failed to resolve reverse citation: {e5}"


def test_phase3_statute_coverage():
    """Verify all 10 core Turkish statutes are recognized with article citations."""
    resolver = LegalEntityResolver()
    statute_cases = [
        ("TBK 117", "TBK", "117"),
        ("TMK 117", "TMK", "117"),
        ("TCK 86", "TCK", "86"),
        ("CMK 141", "CMK", "141"),
        ("HMK 86", "HMK", "86"),
        ("TTK 18", "TTK", "18"),
        ("İYUK 7", "İYUK", "7"),
        ("KVKK 11", "KVKK", "11"),
        ("İş Kanunu 17", "İş Kanunu", "17"),
        ("İŞ KANUNU 25", "İş Kanunu", "25"),
        ("Anayasa 141", "Anayasa", "141"),
    ]
    for text, expected_code, expected_art in statute_cases:
        resolved = resolver.extract_entities(text)
        assert any(
            expected_code in x and expected_art in x for x in resolved
        ), f"Failed on '{text}'. Expected {expected_code} with art {expected_art}, got {resolved}"


def test_phase3_same_article_different_statute_no_cartesian_collision():
    """Verify no Cartesian collision between multiple statutes and articles in one text."""
    resolver = LegalEntityResolver()

    # Query with two statutes and different articles
    text = "TBK 117 uyarınca temerrüt ve TMK 50 tüzel kişiler"
    resolved = resolver.extract_entities(text)

    # Must contain TBK m.117 and TMK m.50
    assert any(
        "TBK" in x and "117" in x for x in resolved
    ), f"Missing TBK 117 in {resolved}"
    assert any(
        "TMK" in x and "50" in x for x in resolved
    ), f"Missing TMK 50 in {resolved}"

    # Must NOT contain cross-polluted pairs: TBK 50 or TMK 117!
    assert not any(
        "TBK" in x and "50" in x for x in resolved
    ), f"Illegal collision: TBK 50 found in {resolved}"
    assert not any(
        "TMK" in x and "117" in x for x in resolved
    ), f"Illegal collision: TMK 117 found in {resolved}"


def test_phase3_structured_citations():
    """Verify structured citations extraction."""
    resolver = LegalEntityResolver()
    citations = resolver.extract_citations(
        "CMK 141 tazminat vs TCK 141 hırsızlık vs Anayasa 141 aleniyet"
    )
    assert len(citations) == 3

    statutes = {c.statute_code: c.article for c in citations}
    assert statutes == {"CMK": "141", "TCK": "141", "Anayasa": "141"}


async def _seed_legal_assertion(
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
):
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="workspace-p3",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="revision-p3",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-p3",
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
async def test_phase3_downstream_same_article_disambiguation(tmp_path):
    """Verify that when query specifies TBK 117, TBK 117 is boosted and competing TMK 117/TCK 117 are penalized."""
    db_path = str(tmp_path / "phase3_test.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
    )

    dao = MemoryDAO(engine, vector, graph)
    tenant_id = "tenant-p3"
    agent_id = "agent-p3"
    dataset_id = "dataset-p3"

    try:
        # Seed 3 competing articles with the same article number 117
        ass_tbk = await _seed_legal_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="tbk-doc-117",
            subject="TBK m.117",
            predicate="hükmü",
            literal_value="Borçlunun temerrüdü ve ihtar şartı",
            evidence_span="Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer.",
            raw_log_id=1,
        )

        ass_tmk = await _seed_legal_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="tmk-doc-117",
            subject="TMK m.117",
            predicate="hükmü",
            literal_value="Vesayet dairelerinin sorumluluğu ve atanması",
            evidence_span="Vesayet daireleri, vesayet altındaki kişinin menfaatlerini gözetmekle yükümlüdür.",
            raw_log_id=2,
        )

        ass_tck = await _seed_legal_assertion(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="tck-doc-117",
            subject="TCK m.117",
            predicate="hükmü",
            literal_value="İş ve çalışma hürriyetinin ihlali suçu",
            evidence_span="Cebir veya tehdit kullanarak iş ve çalışma hürriyetini ihlal eden kimseye ceza verilir.",
            raw_log_id=3,
        )

        # Mock vector search to return all 3 with equal distances (TMK first, then TBK, then TCK)
        vector.search.return_value = [
            {"node_id": ass_tmk["assertion_id"], "_distance": 0.20},
            {"node_id": ass_tbk["assertion_id"], "_distance": 0.20},
            {"node_id": ass_tck["assertion_id"], "_distance": 0.20},
        ]

        # Query for TBK 117
        results = await dao.search_v4_memory(
            query="TBK 117 borçlunun temerrüdü ve ihtarı",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        # Top result must be TBK 117!
        top_hit = results[0]
        assert top_hit["assertion_id"] == ass_tbk["assertion_id"]
        assert top_hit["legal_factor"] > 1.0

        # Competing TMK/TCK hits must have penalized legal_factor
        for r in results[1:]:
            if r["assertion_id"] in (ass_tmk["assertion_id"], ass_tck["assertion_id"]):
                assert (
                    r["legal_factor"] < 1.0
                ), f"Expected penalized legal_factor, got {r['legal_factor']}"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase3_ingestion_uses_structural_legal_identity(tmp_path):
    engine = AsyncEngine(str(tmp_path / "phase3-ingestion-identity.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
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
    )
    dao = MemoryDAO(engine, vector, graph)

    try:
        assertion = await _seed_legal_assertion(
            dao,
            tenant_id="tenant-p3-identity",
            agent_id="agent-p3-identity",
            dataset_id="dataset-p3-identity",
            doc_id="doc-p3-identity",
            subject="Türk Borçlar Kanunu madde 117",
            predicate="hükmü",
            literal_value="Temerrüt",
            evidence_span="TBK 117 temerrüt kuralı.",
            raw_log_id=301,
        )
        async with engine.connection() as db:
            async with db.execute(
                "SELECT canonical_name FROM v4_entities WHERE entity_id = ?",
                (assertion["subject_id"],),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "TBK m.117"
    finally:
        await engine.close()
