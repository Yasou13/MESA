"""Held-out Round 2 counterexamples independent from the repair reproducer."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import _triplets

FIXTURE = Path(__file__).parent / "fixtures" / "v4_round2_legal_dev.json"


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
        search_v4_graph=AsyncMock(return_value=[]),
        is_operational=True,
    )
    return MemoryDAO(engine, vector, graph)


async def _seed(
    dao: MemoryDAO,
    *,
    raw_log_id: int,
    document_id: str,
    version_id: str,
    content: str,
    valid_from: str,
    valid_to: str,
) -> dict:
    metadata = {
        "_mesa_legal_identity": {
            "law_id": "6098",
            "statute_code": "TBK",
            "article_id": "1",
            "version_id": version_id,
            "identity_source": "catalog",
        },
        "jurisdiction": "TR",
        "valid_from": valid_from,
        "valid_to": valid_to,
    }
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id="tenant-holdout",
        workspace_id="workspace-holdout",
        dataset_id="dataset-holdout",
        document_id=document_id,
        revision_id=f"{document_id}:revision:1",
        chunk_id=f"{document_id}:chunk:1",
        source_ref=f"https://example.invalid/round2/{document_id}",
        agent_id="agent-holdout",
        session_id="session-holdout",
        content_payload=content,
        evidence_span=content,
        metadata=metadata,
        embedding_provider="test",
        embedding_model="round2-holdout",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:round2-holdout:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(candidate, raw_log_id=raw_log_id)
    mutation = await dao.get_projection_mutation(candidate["mutation_id"])
    assert mutation is not None
    await dao.project_v4_graph_triplet(
        mutation=mutation, triplet=_triplets(mutation)[0]
    )
    assertions = await dao.list_v4_assertions_for_mutation(mutation["mutation_id"])
    assert len(assertions) == 1
    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mutation["mutation_id"],),
        )
        await db.commit()
    return assertions[0]


def test_round2_dev_fixture_is_locked_disjoint_and_complete() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["locked"] is True
    assert fixture["top_k"] == 5
    assert "official Profile B" in fixture["purpose"]
    expected = {
        "DIRECT_ARTICLE_ID",
        "SEMANTIC_PARAPHRASE",
        "FALSE_FRIEND",
        "ALIAS_DISAMBIGUATION",
        "TEMPORAL_VERSION",
        "MULTI_HOP_COMPLETE",
        "GRAPH_SCOPE_NEGATIVE",
        "NO_ANSWER",
        "RESTART_PROJECTION",
        "VECTOR_SPACE_MISMATCH",
    }
    query_ids = [item["query_id"] for item in fixture["queries"]]
    assert set(query_ids) == expected
    assert len(query_ids) == len(set(query_ids))


@pytest.mark.asyncio
async def test_typed_identity_respects_historic_and_current_validity_windows(
    tmp_path,
) -> None:
    """Fresh skeptic case: equal law/article identities cannot mix revisions."""
    database = tmp_path / "round2-holdout-temporal.sqlite"
    engine = AsyncEngine(str(database))
    await engine.initialize()
    await initialize_schema(engine)
    dao = _make_dao(engine)
    try:
        historic = await _seed(
            dao,
            raw_log_id=101,
            document_id="tbk-historic-1",
            version_id="historic-2024",
            content="MADDE 1 — tarihsel DEV hükmü.",
            valid_from="2020-01-01T00:00:00Z",
            valid_to="2024-12-31T23:59:59Z",
        )
        current = await _seed(
            dao,
            raw_log_id=102,
            document_id="tbk-current-1",
            version_id="current-2025",
            content="MADDE 1 — güncel DEV hükmü.",
            valid_from="2025-01-01T00:00:00Z",
            valid_to="",
        )
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE v4_assertions SET status = 'SUPERSEDED' "
                "WHERE assertion_id = ?",
                (historic["assertion_id"],),
            )
            await db.commit()

        current_results = await dao.search_v4_memory(
            tenant_id="tenant-holdout",
            agent_id="agent-holdout",
            dataset_ids=["dataset-holdout"],
            query="TBK madde 1",
            jurisdiction="TR",
            limit=5,
            graph_enabled=False,
        )
        historic_results = await dao.search_v4_memory(
            tenant_id="tenant-holdout",
            agent_id="agent-holdout",
            dataset_ids=["dataset-holdout"],
            query="TBK madde 1",
            jurisdiction="TR",
            valid_at="2024-06-01T00:00:00Z",
            limit=5,
            graph_enabled=False,
        )

        assert [row["assertion_id"] for row in current_results] == [
            current["assertion_id"]
        ]
        assert [row["assertion_id"] for row in historic_results] == [
            historic["assertion_id"]
        ]
        assert (
            current_results[0]["retrieval_provenance"]["legal_identity"]["version_id"]
            == "current-2025"
        )
        assert (
            historic_results[0]["retrieval_provenance"]["legal_identity"]["version_id"]
            == "historic-2024"
        )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_typed_identity_remains_queryable_after_engine_restart(tmp_path) -> None:
    database = tmp_path / "round2-holdout-restart.sqlite"
    first_engine = AsyncEngine(str(database))
    await first_engine.initialize()
    await initialize_schema(first_engine)
    first_dao = _make_dao(first_engine)
    expected = await _seed(
        first_dao,
        raw_log_id=201,
        document_id="tbk-restart-1",
        version_id="restart-v1",
        content="MADDE 1 — yeniden başlatma DEV hükmü.",
        valid_from="2025-01-01T00:00:00Z",
        valid_to="",
    )
    await first_engine.close()

    restarted_engine = AsyncEngine(str(database))
    await restarted_engine.initialize()
    restarted_dao = _make_dao(restarted_engine)
    try:
        results = await restarted_dao.search_v4_memory(
            tenant_id="tenant-holdout",
            agent_id="agent-holdout",
            dataset_ids=["dataset-holdout"],
            query="6098 sayılı Türk Borçlar Kanunu madde 1",
            jurisdiction="TR",
            limit=1,
            graph_enabled=False,
        )
        assert [row["assertion_id"] for row in results] == [expected["assertion_id"]]
        assert (
            results[0]["retrieval_provenance"]["legal_identity"]["version_id"]
            == "restart-v1"
        )
    finally:
        await restarted_engine.close()
