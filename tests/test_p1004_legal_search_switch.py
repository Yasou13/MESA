"""D49: disabling legal processing also applies to already indexed documents."""

import json

import pytest

import mesa_storage.dao as dao_module
from mesa_storage import legal_rules
from mesa_storage.legal_identity import LegalEntityResolver
from tests.test_document_ingestion import _add, _rows
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime

_IDENTITY_COLUMNS = (
    "law_id", "statute_code", "article_id", "paragraph_id", "clause_id",
    "legal_version_id", "legal_identity_source",
)


def _forbidden(*args, **kwargs):
    raise AssertionError("legal processing ran with MESA_LEGAL_RULES_ENABLED=false")


@pytest.mark.asyncio
async def test_disabled_search_matches_untyped_store(
    model_runtime, monkeypatch
):
    async with model_runtime() as env:
        document = await _add(
            env,
            "Türk Borçlar Kanunu\nKanun Numarası: 6098\n"
            "Yargıtay 3. Hukuk Dairesi 2024/123 E. 2025/456 K.\n"
            "MADDE 49- TBK tazminat hükmü.\n"
            "MADDE 50- TBK ispat hükmü.\n"
            "MADDE 51- TBK belirleme hükmü.\n",
            title="Türk Borçlar Kanunu TBK",
        )
        await _consume(env, document["chunk_count"] + 3)
        assert any(a["law_id"] for a in await _rows(env, "v4_assertions"))
        assert any(
            json.loads(m["metadata_json"]).get("_mesa_legal_items")
            for m in await _rows(env, "memory_mutations")
        )
        await env.restart(MESA_LEGAL_RULES_ENABLED=False)
        monkeypatch.setattr(dao_module, "_assertion_legal_identity", _forbidden)
        monkeypatch.setattr(LegalEntityResolver, "__init__", _forbidden)
        monkeypatch.setattr(legal_rules, "extract", _forbidden)

        async def search(query):
            return await env.dao.search_v4_memory(
                tenant_id="model-tenant", agent_id="model-agent",
                dataset_ids=["main"], query=query, limit=50,
            )

        queries = ["TBK m. 49", "2024/123 E. 2025/456 K."]
        with_identities = [await search(query) for query in queries]
        assert all(with_identities)
        for results in with_identities:
            for result in results:
                assert result["legal_factor"] == 1.0
                assert not {"exact_match", "legal_items", "legal_identity"}.intersection(
                    result["retrieval_provenance"]
                )

        # Same physical IDs avoid the known cross-store UUID tie difference.
        # Remove only the durable overlays absent after switch-off ingestion.
        async with env.dao._sql.connection() as db:
            assignments = ", ".join(f"{column}=''" for column in _IDENTITY_COLUMNS)
            await db.execute(f"UPDATE v4_assertions SET {assignments}")
            for mutation in await _rows(env, "memory_mutations"):
                metadata = json.loads(mutation["metadata_json"])
                metadata.pop("_mesa_legal_items", None)
                metadata.pop("_mesa_legal_identity", None)
                await db.execute(
                    "UPDATE memory_mutations SET metadata_json=? WHERE mutation_id=?",
                    (json.dumps(metadata), mutation["mutation_id"]),
                )
            await db.commit()
        await env.restart()
        without_identities = [await search(query) for query in queries]
        assert with_identities == without_identities
