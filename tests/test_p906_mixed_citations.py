"""Mask store names without changing the spelling of independent citations."""

import pytest

from mesa_storage.legal_identity import LegalEntityResolver
from tests.test_document_ingestion import _add
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


def _pairs(resolver, query):
    return {(c.statute_code, c.article) for c in resolver.extract_citations(query)}


@pytest.mark.parametrize(
    "query",
    [
        "Tebligat Kanunu 21. madde ve 6325 sayılı Kanunun 2. maddesi",
        " \tTEBLİGAT   K.\n21. madde ve 6325 sayılı Kanunun 2. maddesi  ",
        "6325 sayılı Kanunun 2. maddesi ile Tebligat K. 21. madde",
    ],
)
def test_numbered_citation_survives_store_name_and_offset_changes(query):
    resolver = LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"})
    assert _pairs(resolver, query) == {("7201", "21"), ("6325", "2")}


def test_ontology_alias_number_and_store_name_are_all_kept():
    resolver = LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"})
    query = "TCK m. 81 ve Tebligat Kanunu 21. madde ile 6325 sayılı Kanunun 2. maddesi"
    assert _pairs(resolver, query) == {("TCK", "81"), ("7201", "21"), ("6325", "2")}


def test_numbered_law_and_decomposed_turkish_store_name():
    resolver = LegalEntityResolver(law_names={"Zümrüt Taşları Kanunu": "8888"})
    query = "5237 sayılı Kanunun 81. maddesi ile Zu\u0308mru\u0308t Tas\u0327ları K. 3. madde"
    assert _pairs(resolver, query) == {("TCK", "81"), ("8888", "3")}


@pytest.mark.parametrize(
    "numbered",
    [
        "6325 sayılı Kanunun 2. maddesi",
        "6325 sayılı Kanun m. 2",
        "6325 sayılı Kanun’un 2. maddesi",
        "6325 sayılı Hukuk Uyuşmazlıklarında Arabuluculuk Kanununun 2 nci maddesi",
        # A newline inside the title must keep the same rejection as without a store name.
        "6325 sayılı Hukuk\nUyuşmazlıklarında Arabuluculuk Kanununun 2 nci maddesi",
    ],
)
def test_numbered_grammar_is_identical_with_and_without_a_store_name(numbered):
    expected = _pairs(LegalEntityResolver(), numbered)
    resolver = LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"})
    combined = _pairs(resolver, numbered + " ve Tebligat K. 21. madde")
    assert combined == expected | {("7201", "21")}


@pytest.mark.asyncio
async def test_both_typed_articles_use_the_existing_exact_slot(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        first = await _add(
            env,
            "Kanun Numarası: 7201\nMADDE 21- Tebligat hükmü.\n"
            "MADDE 22- Kural.\nMADDE 23- Kural.\n",
            document_id="tebligat",
            title="Tebligat Kanunu",
        )
        second = await _add(
            env,
            "Kanun Numarası: 6325\nMADDE 2- Arabuluculuk hükmü.\n"
            "MADDE 3- Kural.\nMADDE 4- Kural.\n",
            document_id="arabuluculuk",
            title="Arabuluculuk Kanunu",
        )
        await _consume(env, first["chunk_count"] + second["chunk_count"] + 3)
        results = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="Tebligat Kanunu 21. madde ve 6325 sayılı Kanunun 2. maddesi",
            graph_enabled=False,
            limit=10,
        )
        promoted = [r for r in results if "exact_match" in r["retrieval_provenance"]]
        assert len(promoted) == 2
        assert {
            (
                r["retrieval_provenance"]["legal_identity"]["law_id"],
                r["retrieval_provenance"]["legal_identity"]["article_id"],
            )
            for r in promoted
        } == {("7201", "21"), ("6325", "2")}
        assert [
            r["retrieval_provenance"]["exact_match"]["order"] for r in promoted
        ] == [0, 1]
