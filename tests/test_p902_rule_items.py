"""Runtime-owned rule items are source evidence, never ranking inputs."""

import copy
import json

import pytest

from mesa_memory.config import config
from mesa_storage.legal_rules import RULES_VERSION, extract
from mesa_workers.projection_worker import _triplets
from tests.test_document_ingestion import _add, _rows
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime

TEXT = "15.03.2024 tarihinde TBK m. 49 uygulanır."


async def _admit(env, metadata):
    return await env.dao.admit_v4_memory(
        tenant_id="model-tenant",
        workspace_id="default",
        dataset_id="main",
        agent_id="model-agent",
        session_id=env.session_id,
        document_id="rules-document",
        revision_id="rules-revision",
        chunk_id="rules-chunk",
        title="Rules",
        content_payload=TEXT,
        source_ref="file://rules.txt",
        evidence_span=TEXT,
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata=metadata,
        legal_identity={"law_id": "6098", "article_id": "1"},
        embedding_provider="test",
        embedding_model="rules",
        embedding_version="v1",
        embedding_dimension=2,
        validation_mode=0,
        policy=config.queue_admission_policy,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_items_are_stored_and_returned_with_chunk_offsets(model_runtime, enabled):
    async with model_runtime(
        MESA_LEGAL_RULES_ENABLED=enabled, MESA_GRAPH_ENABLED=False
    ) as env:
        text = (
            "Kanun Numarası: 6098\nMADDE 1- "
            + TEXT
            + "\nMADDE 2- Kural.\nMADDE 3- Kural.\n"
        )
        added = await _add(env, text)
        await _consume(env, added["chunk_count"] + 2)
        mutations = await _rows(env, "memory_mutations")
        stored = {}
        for mutation in mutations:
            item_set = json.loads(mutation["metadata_json"]).get("_mesa_legal_items")
            if enabled:
                assert item_set == {
                    "version": RULES_VERSION,
                    "items": extract(mutation["content_payload"]),
                }
                for item in item_set["items"]:
                    assert (
                        mutation["content_payload"][item["start"] : item["end"]]
                        == item["text"]
                    )
            else:
                assert item_set is None
            stored[mutation["mutation_id"]] = item_set
        if enabled:
            assert {
                item["type"] for value in stored.values() for item in value["items"]
            } >= {"date", "citation"}
        for raw in await _rows(env, "raw_logs"):
            assert (
                json.loads(raw["payload"])["metadata"].get("_mesa_legal_items")
                in stored.values()
            )
        results = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="TBK m. 49 15.03.2024",
            limit=50,
            graph_enabled=False,
        )
        assert results
        for result in results:
            mid = result["provenance"][0]["mutation_id"]
            assert result["retrieval_provenance"].get("legal_items") == stored[mid]
        if not enabled:
            return
        # Remove only stored items: preserve identities, candidates, scores and order.
        before = copy.deepcopy(results)
        for result in before:
            result["retrieval_provenance"].pop("legal_items", None)
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET metadata_json=json_remove(metadata_json,'$._mesa_legal_items')"
            )
            await db.commit()
        after = await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="TBK m. 49 15.03.2024",
            limit=50,
            graph_enabled=False,
        )
        assert after == before


@pytest.mark.asyncio
async def test_admission_replaces_forged_items_and_extraction_keeps_them(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        admitted = await _admit(
            env, {"_mesa_legal_items": {"version": "forged", "items": []}}
        )
        mid = admitted["response"]["mutation_id"]
        expected = {"version": RULES_VERSION, "items": extract(TEXT)}
        metadata = json.loads(
            (await _rows(env, "memory_mutations"))[0]["metadata_json"]
        )
        assert metadata["_mesa_legal_items"] == expected
        assert await env.dao.record_mutation_extraction(
            "model-agent",
            mid,
            [
                {
                    "head": "Kişi",
                    "relation": "uygular",
                    "tail": None,
                    "literal_value": "ilk hüküm",
                    "source_span": TEXT,
                },
                {
                    "head": "Kişi",
                    "relation": "uygular",
                    "tail": None,
                    "literal_value": "ikinci hüküm",
                    "source_span": TEXT,
                },
            ],
        )
        metadata = json.loads(
            (await _rows(env, "memory_mutations"))[0]["metadata_json"]
        )
        assert metadata["_mesa_legal_items"] == expected
        mutation = await env.dao.get_projection_mutation(mid)
        triplets = _triplets(mutation)
        assert len(triplets) == 2
        for triplet in triplets:
            await env.dao.project_v4_sql_entity(
                mutation=mutation, entity_name=triplet["head"]
            )
            await env.dao.project_v4_sql_assertion(mutation=mutation, triplet=triplet)
        assertions = await env.dao.list_v4_assertions_for_mutation(mid)
        assert len(assertions) == 2
        assert {
            (a["law_id"], a["statute_code"], a["article_id"]) for a in assertions
        } == {("6098", "TBK", "1")}


@pytest.mark.asyncio
async def test_document_purge_erases_rule_quotes_in_both_ledgers(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _add(env, TEXT)
        await _consume(env, 3)
        # Seed a runtime-owned item snapshot explicitly so this privacy test also
        # covers upgraded stores independently of the admission implementation.
        item_set = json.dumps({"version": RULES_VERSION, "items": extract(TEXT)})
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET metadata_json=json_set(metadata_json,'$._mesa_legal_items',json(?))",
                (item_set,),
            )
            await db.execute(
                "UPDATE raw_logs SET payload=json_set(payload,'$.metadata._mesa_legal_items',json(?))",
                (item_set,),
            )
            await db.commit()
        await env.dao.purge_v4_document(
            tenant_id="model-tenant", dataset_id="main", document_id="whole-document"
        )
        for row in await _rows(env, "memory_mutations"):
            assert "_mesa_legal_items" not in json.loads(row["metadata_json"])
        for row in await _rows(env, "raw_logs"):
            assert "_mesa_legal_items" not in json.loads(row["payload"])["metadata"]
