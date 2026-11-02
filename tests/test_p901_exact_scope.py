"""Store-derived law names and the bounded exact-match-first slot."""

import pytest

from mesa_storage.legal_identity import LegalEntityResolver
from tests.test_document_ingestion import _add
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Kamulaştırma Kanunu 35. madde", [("2942", "35")]),
        ("TEBLİGAT K. 21. madde", [("7201", "21")]),
        ("Tebligat Kanunu'nun 21. maddesi", [("7201", "21")]),
        ("Tebligat Kanununun 21 inci maddesi", [("7201", "21")]),
        ("Borçlar Kanunu m. 49", [("818", "49")]),
        ("Türk Borçlar Kanunu m. 49", [("TBK", "49")]),
        ("2017 yılında 5 maddelik bir düzenleme", []),
    ],
)
def test_scope_names_override_only_the_longest_exact_name(query, expected):
    resolver = LegalEntityResolver(
        law_names={
            "Kamulaştırma Kanunu": "2942",
            "Tebligat Kanunu": "7201",
            "Borçlar Kanunu": "818",
            "Türk Borçlar Kanunu": "6098",
        }
    )
    assert [
        (c.statute_code, c.article) for c in resolver.extract_citations(query)
    ] == expected


def test_no_store_name_no_new_name_resolution():
    assert LegalEntityResolver().extract_citations("Tebligat K. 21. madde") == []
    assert [
        (c.statute_code, c.article)
        for c in LegalEntityResolver().extract_citations("Borçlar Kanunu 49")
    ] == [("TBK", "49")]


@pytest.mark.asyncio
async def test_exact_slot_is_bounded_in_document_order_and_preserves_other_order(
    model_runtime, monkeypatch
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        text = "TEBLİGAT KANUNU1\nKanun Numarası: 7201\nÖnsöz.\n" + (
            "MADDE 21- " + "Tebligat hükmü. " * 800 + "\n"
            "MADDE 22- Tebligat hükmü.\nMADDE 23- Tebligat hükmü.\n"
        )
        added = await _add(env, text, title="opaque.txt")
        await _consume(env, added["chunk_count"] + 2)

        async def search(query):
            return await env.dao.search_v4_memory(
                tenant_id="model-tenant",
                agent_id="model-agent",
                dataset_ids=["main"],
                query=query,
                limit=50,
                graph_enabled=False,
            )

        result = await search("Tebligat K. 21. madde")
        expected = [
            m
            for m in added["mutation_ids"]
            if (
                (await env.dao.get_projection_mutation(m)).get("legal_identity") or {}
            ).get("article_id")
            == "21"
        ]
        assert len(expected) > 3
        assert [r["provenance"][0]["mutation_id"] for r in result[:3]] == expected[:3]
        assert [
            r["retrieval_provenance"]["exact_match"]["order"] for r in result[:3]
        ] == [0, 1, 2]
        assert sum("exact_match" in r["retrieval_provenance"] for r in result) == 3
        remainder = [
            r for r in result if "exact_match" not in r["retrieval_provenance"]
        ]
        assert remainder == sorted(
            remainder, key=lambda r: (-r["final_score"], r["candidate_id"])
        )
        for query in ("Tebligat K. 9999. madde", "99999 sayılı Kanunun 21. maddesi"):
            assert all(
                "exact_match" not in r["retrieval_provenance"]
                for r in await search(query)
            )
        # The revision name/order cache is reused, and purging invalidates it.
        scope = next(iter(env.dao._search_scope_cache.values()))
        assert "legal_context" in scope
        before = await search("Tebligat K. 21. madde")
        assert [r["candidate_id"] for r in before] == [
            r["candidate_id"] for r in result
        ]
        await env.dao.purge_v4_document(
            tenant_id="model-tenant", dataset_id="main", document_id="whole-document"
        )
        assert await search("Tebligat K. 21. madde") == []


@pytest.mark.asyncio
async def test_store_names_cannot_cross_dataset_scope(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        content = "Kanun Numarası: 2942\nMADDE 1- Kural.\nMADDE 2- Kural.\nMADDE 35- Kamulaştırma.\n"
        added = await _add(env, content, title="Kamulaştırma Kanunu")
        await _consume(env, added["chunk_count"] + 2)
        matching = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="Kamulaştırma Kanunu 35. madde",
            graph_enabled=False,
        )
        assert matching[0]["retrieval_provenance"]["legal_identity"]["law_id"] == "2942"
        assert (
            matching[0]["retrieval_provenance"]["legal_identity"]["article_id"] == "35"
        )
        await env.dao.ensure_v4_catalog_scope(
            tenant_id="model-tenant", workspace_id="default", dataset_id="empty"
        )
        result = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["empty"],
            query="Kamulaştırma Kanunu 35. madde",
            graph_enabled=False,
        )
        assert result == []
        empty_scope = next(
            s for k, s in env.dao._search_scope_cache.items() if k[1] == ("empty",)
        )
        assert not empty_scope["legal_context"]["names"]
