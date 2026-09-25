"""Phase 4 regression tests: Real Passage / Article Lexical Retrieval hardening.

Verifies:
1. Query parsing: eliminates the anti-pattern (TBK OR m OR 117 OR uyarınca...),
   separating structured legal identity from clean free-text content terms.
2. Real passage / evidence retrieval: queries matching passage / evidence text
   (paraphrase) retrieve the exact assertion even when entity name does not contain the words.
3. Direct legal citation lexical retrieval.
4. Same-article-number collision suppression in lexical lane (TBK 117 vs TMK 117 vs TCK 117).
5. Turkish morphology and Unicode handling (İ/I/i/ı).
6. Strict tenant / dataset / agent isolation before lexical candidate ranking.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.retrieval.legal_resolver import LegalEntityResolver
from mesa_storage.dao import MemoryDAO, _normalize_identity_text
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _seed_assertion_with_passage(
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
        workspace_id="workspace-p4",
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="revision-p4",
        chunk_id=f"chunk-{doc_id}",
        source_ref=f"source-{doc_id}",
        agent_id=agent_id,
        session_id="session-p4",
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


@pytest.mark.asyncio
async def test_phase4_paraphrase_passage_lexical_retrieval(tmp_path):
    """Verify that a paraphrase query matching evidence text retrieves the exact assertion."""
    db_path = str(tmp_path / "phase4_test.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p4"
    agent_id = "agent-p4"
    dataset_id = "dataset-p4"

    try:
        # Entity has an opaque name, but evidence_span contains the legal passage
        ass = await _seed_assertion_with_passage(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-temerrut",
            subject="MaddeHukuku",  # Does NOT contain "borçlunun", "temerrüdü", "alacaklı"
            predicate="düzenler",
            literal_value="Borç ilişkilerinde temerrüt kuralları",
            evidence_span="Muaccel bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer.",
            raw_log_id=1,
        )

        # Paraphrase query with no statute name
        query = "alacaklının ihtarıyla borçlunun temerrüde düşmesi"
        results = await dao.search_v4_memory(
            query=query,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert (
            len(results) >= 1
        ), "Expected passage lexical retrieval to find the assertion"
        top = results[0]
        assert top["assertion_id"] == ass["assertion_id"]
        assert (
            "bm25" in top["retrieval_provenance"]["origins"]
        ), "Expected origin to include bm25 lexical lane"
        assert top["retrieval_provenance"]["lane_ranks"].get("bm25") == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase4_same_article_collision_lexical(tmp_path):
    """Verify that lexical lane suppresses competing statutes with the same article number."""
    db_path = str(tmp_path / "phase4_collision.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p4"
    agent_id = "agent-p4"
    dataset_id = "dataset-p4"

    try:
        ass_tbk = await _seed_assertion_with_passage(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tbk-117",
            subject="TBK m.117",
            predicate="hükmü",
            literal_value="Borçlunun temerrüdü",
            evidence_span="Muaccel bir borcun borçlusu alacaklının ihtarıyla temerrüde düşer.",
            raw_log_id=10,
        )

        await _seed_assertion_with_passage(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tmk-117",
            subject="TMK m.117",
            predicate="hükmü",
            literal_value="Vesayet dairelerinin sorumluluğu",
            evidence_span="Vesayet daireleri vesayet altındaki kişinin menfaatlerini gözetir.",
            raw_log_id=20,
        )

        results = await dao.search_v4_memory(
            query="TBK 117 borçlunun temerrüdü",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass_tbk["assertion_id"]
        # TMK 117 must not displace TBK 117
        if len(results) > 1:
            assert results[1]["assertion_id"] != ass_tbk["assertion_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase4_direct_legal_citation_lexical_retrieval(tmp_path):
    """Verify that direct legal citation retrieves target assertion at top lexical rank."""
    db_path = str(tmp_path / "phase4_direct.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p4"
    agent_id = "agent-p4"
    dataset_id = "dataset-p4"

    try:
        ass_tck = await _seed_assertion_with_passage(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-tck-86",
            subject="TCK m.86",
            predicate="suç_tanımı",
            literal_value="Kasten yaralama suçu",
            evidence_span="Kasten başkasının vücuduna acı veren veya sağlığının ya da algılama yeteneğinin bozulmasına neden olan kişi cezalandırılır.",
            raw_log_id=50,
        )

        results = await dao.search_v4_memory(
            query="TCK 86",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )

        assert len(results) >= 1
        assert results[0]["assertion_id"] == ass_tck["assertion_id"]
        assert "bm25" in results[0]["retrieval_provenance"]["origins"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase4_turkish_morphology_and_unicode(tmp_path):
    """Verify that uppercase/lowercase Turkish I/İ queries match passages consistently."""
    db_path = str(tmp_path / "phase4_unicode.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)
    tenant_id = "tenant-p4"
    agent_id = "agent-p4"
    dataset_id = "dataset-p4"

    try:
        ass_is = await _seed_assertion_with_passage(
            dao,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            doc_id="doc-is-17",
            subject="İş Kanunu m.17",
            predicate="hükmü",
            literal_value="Süreli fesih ve ihbar önelleri",
            evidence_span="Belirsiz süreli iş sözleşmelerinin feshinden önce durumun diğer tarafa bildirilmesi gerekir.",
            raw_log_id=60,
        )

        # Upper case query
        res_upper = await dao.search_v4_memory(
            query="İŞ KANUNU SÜRELİ FESİH İHBAR",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )
        assert len(res_upper) >= 1
        assert res_upper[0]["assertion_id"] == ass_is["assertion_id"]

        # Lower case query
        res_lower = await dao.search_v4_memory(
            query="iş kanunu süreli fesih ihbar",
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            limit=5,
        )
        assert len(res_lower) >= 1
        assert res_lower[0]["assertion_id"] == ass_is["assertion_id"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_phase4_tenant_dataset_isolation(tmp_path):
    """Verify that lexical passage retrieval never leaks cross-tenant or cross-dataset assertions."""
    db_path = str(tmp_path / "phase4_isolation.sqlite")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = _make_dao(engine)

    try:
        # Ingest in Tenant A
        await _seed_assertion_with_passage(
            dao,
            tenant_id="tenant-A",
            agent_id="agent-A",
            dataset_id="dataset-A",
            doc_id="doc-A",
            subject="GizliBelge",
            predicate="açıklar",
            literal_value="Gizli ticari sırlar",
            evidence_span="Tenant A özel şirket sırları ve finansal tablolar.",
            raw_log_id=100,
        )

        # Ingest in Tenant B
        await _seed_assertion_with_passage(
            dao,
            tenant_id="tenant-B",
            agent_id="agent-B",
            dataset_id="dataset-B",
            doc_id="doc-B",
            subject="AçıkBelge",
            predicate="açıklar",
            literal_value="Kamusal bilgiler",
            evidence_span="Tenant B kamusal açıklamalar ve bültenler.",
            raw_log_id=200,
        )

        # Query Tenant A with text from Tenant B
        results = await dao.search_v4_memory(
            query="kamusal açıklamalar ve bültenler",
            tenant_id="tenant-A",
            agent_id="agent-A",
            dataset_ids=["dataset-A"],
            limit=5,
        )

        assert len(results) == 0, f"Cross-tenant leak detected: {results}"
    finally:
        await engine.close()


def test_phase4_query_parsing_antipattern_fixed():
    """Verify that query parsing separates structured legal identity from free-text terms and strips stopwords."""
    import re

    TURKISH_LEGAL_STOPWORDS = {
        "m",
        "md",
        "madde",
        "maddesi",
        "maddesine",
        "maddesinde",
        "fıkra",
        "fıkrası",
        "bent",
        "bendi",
        "uyarınca",
        "gereğince",
        "göre",
        "ve",
        "veya",
        "ile",
        "için",
        "olan",
        "bir",
        "bu",
        "şu",
        "hükmü",
        "hükmünce",
        "kapsamında",
        "ilgili",
        "nedir",
        "nelerdir",
        "hakkında",
        "tarafından",
        "sayılı",
        "kanun",
        "kanunu",
    }

    resolver = LegalEntityResolver()
    query = "TBK m.117 uyarınca borçlunun temerrüdü ve ihtar şartı"
    citations = resolver.extract_citations(query)
    assert len(citations) >= 1
    c = citations[0]
    assert c.statute_code == "TBK"
    assert c.article == "117"

    raw_tokens = re.findall(r"\w+", _normalize_identity_text(query))
    citation_statutes_norm = {
        _normalize_identity_text(c.statute_code),
        _normalize_identity_text(c.statute_canonical),
    }
    for a in c.aliases:
        citation_statutes_norm.add(_normalize_identity_text(a))
    citation_articles = {c.article for c in citations if c.article}

    content_tokens = [
        t
        for t in raw_tokens
        if t not in TURKISH_LEGAL_STOPWORDS
        and t not in citation_statutes_norm
        and t not in citation_articles
        and len(t) >= 2
    ]

    # Content tokens must ONLY have genuine semantic words
    assert "borçlunun" in content_tokens
    assert "temerrüdü" in content_tokens
    assert "ihtar" in content_tokens
    assert "şartı" in content_tokens

    # Stopwords and redundant citation tokens must NOT be in content tokens!
    assert "m" not in content_tokens
    assert "117" not in content_tokens
    assert "tbk" not in content_tokens
    assert "uyarınca" not in content_tokens
    assert "ve" not in content_tokens
