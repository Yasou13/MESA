"""Only an authorized decision's own first-chunk header can use the exact slot."""

import copy

import pytest

from tests.test_document_ingestion import _add
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime

REF = "Ceza Genel Kurulu 2017/885 E., 2021/693 K."
OTHER = "Hukuk Genel Kurulu 2000/11 E., 2001/22 K."


async def _search(env, query, **scope):
    params = dict(
        tenant_id="model-tenant",
        agent_id="model-agent",
        dataset_ids=["main"],
        query=query,
        limit=50,
        graph_enabled=False,
    )
    params.update(scope)
    return await env.dao.search_v4_memory(**params)


@pytest.mark.asyncio
async def test_own_header_first_chunk_wins_and_quoted_reference_does_not(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        own = await _add(
            env, REF + "\n" + "Gerekçe açıklanmıştır. " * 600, document_id="own"
        )
        quoted = await _add(
            env,
            OTHER + "\n" + "Açıklama devam eder. " * 100 + REF + " kararı " * 30,
            document_id="quoted",
        )
        await _consume(env, own["chunk_count"] + quoted["chunk_count"] + 3)
        results = await _search(env, REF + " kararı")
        assert results[0]["provenance"][0]["mutation_id"] == own["mutation_ids"][0]
        marked = [r for r in results if "exact_match" in r["retrieval_provenance"]]
        assert len(marked) == 1
        assert marked[0]["retrieval_provenance"]["exact_match"] == {
            "kind": "decision",
            "order": 0,
        }
        before = copy.deepcopy(results)
        context = next(iter(env.dao._search_scope_cache.values()))["legal_context"]
        assert await _search(env, REF + " kararı") == before
        assert (
            next(iter(env.dao._search_scope_cache.values()))["legal_context"] is context
        )
        assert await _search(env, REF, agent_id="other-agent") == []
        await env.dao.ensure_v4_catalog_scope(
            tenant_id="model-tenant", workspace_id="default", dataset_id="other-dataset"
        )
        assert await _search(env, REF, dataset_ids=["other-dataset"]) == []
        await env.dao.ensure_v4_catalog_scope(
            tenant_id="other-tenant", workspace_id="default", dataset_id="main"
        )
        assert await _search(env, REF, tenant_id="other-tenant") == []
        # Out-of-time first chunks must not teach their own reference via a
        # still-authorized continuation or another decision's quotation.
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE v4_assertions SET valid_from='2030-01-01' WHERE mutation_id=?",
                (own["mutation_ids"][0],),
            )
            await db.commit()
        historical = await _search(env, REF + " kararı", valid_at="2020-01-01")
        assert all("exact_match" not in r["retrieval_provenance"] for r in historical)
        await env.dao.purge_v4_document(
            tenant_id="model-tenant", dataset_id="main", document_id="own"
        )
        assert all(
            "exact_match" not in r["retrieval_provenance"]
            for r in await _search(env, REF + " kararı")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,expected", [(299, True), (300, False)])
async def test_own_reference_starts_within_first_300_characters(
    model_runtime, offset, expected
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _add(env, " " * offset + REF + "\nGerekçe.")
        await _consume(env, 3)
        results = await _search(env, REF + " kararı")
        assert (
            any("exact_match" in r["retrieval_provenance"] for r in results) is expected
        )


@pytest.mark.asyncio
async def test_unmatched_reference_preserves_results_without_items(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _add(env, OTHER + "\n" + "Yargılama kararı gerekçesi. " * 80)
        await _consume(env, 3)
        query = REF + " kararı gerekçesi"
        before = copy.deepcopy(await _search(env, query))
        assert before and all(
            "exact_match" not in r["retrieval_provenance"] for r in before
        )
        for row in before:
            row["retrieval_provenance"].pop("legal_items", None)
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET metadata_json=json_remove(metadata_json,'$._mesa_legal_items')"
            )
            await db.commit()
        assert await _search(env, query) == before


@pytest.mark.asyncio
async def test_articles_and_decisions_share_three_unique_chunk_slots(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        law = await _add(
            env,
            "Kanun Numarası: 6098\nMADDE 49- "
            + "Tazminat hükmü uygulanır. " * 800
            + "\nMADDE 50- Kural.\nMADDE 51- Kural.\n",
            document_id="law",
        )
        decision = await _add(env, REF + "\nGerekçe.", document_id="decision")
        await _consume(env, law["chunk_count"] + decision["chunk_count"] + 3)
        results = await _search(env, "TBK m. 49 ve " + REF + " kararı")
        marked = [r for r in results if "exact_match" in r["retrieval_provenance"]]
        assert len(marked) == 3
        assert len({r["source_chunk_id"] for r in marked}) == 3
        assert [r["retrieval_provenance"]["exact_match"]["order"] for r in marked] == [
            0,
            1,
            2,
        ]
        remainder = [
            r for r in results if "exact_match" not in r["retrieval_provenance"]
        ]
        assert remainder == sorted(
            remainder, key=lambda r: (-r["final_score"], r["candidate_id"])
        )
