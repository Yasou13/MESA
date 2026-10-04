"""Regression coverage for authoritative evidence and budget-aware packing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.config import config
from mesa_memory.context_builder import ContextBuilder
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import _triplets, process_projection_outbox_once


def _candidate(rank: int, *, evidence: str) -> dict:
    assertion_id = f"assertion-{rank}"
    return {
        "entity": {"canonical_name": f"Article {rank}"},
        "candidate_id": f"candidate-{rank}",
        "evidence_id": assertion_id,
        "assertion_id": assertion_id,
        "source_chunk_id": f"chunk-{rank}",
        "document_id": f"document-{rank}",
        "rrf_score": 1.0 / (60 + rank),
        "provenance": [
            {
                "assertion_id": assertion_id,
                "subject_name": f"Article {rank}",
                "predicate": "states",
                "literal_value": f"Rule {rank}",
                "direction": "asserted",
                "source_ref": f"source-{rank}",
                "document_id": f"document-{rank}",
                "revision_id": f"revision-{rank}",
                "chunk_id": f"chunk-{rank}",
                "evidence_span": evidence,
                "jurisdiction": "TR",
            }
        ],
        "retrieval_provenance": {
            "origins": ["bm25"],
            "lane_ranks": {"bm25": rank},
        },
    }


def test_short_fallback_evidence_is_unchanged() -> None:
    source = "Kısa Türkçe hukuk kuralı aynen korunur."

    [triplet] = _triplets(
        {
            "document_id": "document-short",
            "chunk_id": "chunk-short",
            "content_payload": source,
            "evidence_span": "",
            "projection_triplets": [],
        }
    )

    assert triplet["fact_text"] == source
    assert triplet["source_span"] == source


@pytest.mark.asyncio
async def test_fallback_projection_preserves_full_multi_paragraph_source_in_context(
    tmp_path, monkeypatch
) -> None:
    """Canonical source after the old boundary must survive through ContextBuilder."""
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
    engine = AsyncEngine(str(tmp_path / "evidence-preservation.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(
        is_initialized=True,
        compute_embedding=AsyncMock(return_value=[0.1, 0.2, 0.3]),
        compute_query_embedding=AsyncMock(return_value=[0.1, 0.2, 0.3]),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
    )
    dao = MemoryDAO(engine, vector, graph)
    later_rule = (
        "SONRAKI_OPERATIF_KURAL: borçluya Türkçe bildirim eksiksiz yapılmalıdır."
    )
    source = (
        "Genel başlangıç kuralı, hükmün kapsamını ve temel uygulama alanını açıklar. "
        "Bu giriş paragrafı tarihsel iki yüz karakter sınırını aşacak kadar ayrıntılı "
        "olmalıdır; ancak tek başına uyuşmazlığın sonucunu belirlemez.\n\n"
        f"{later_rule}\n\n"
        "Ek çözüm yolu olarak mahkeme uygun bir giderim belirleyebilir."
    )
    assert len(source) > 200
    assert source.index(later_rule) > 200

    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-evidence",
            workspace_id="workspace-evidence",
            dataset_id="dataset-evidence",
        )
        admission = await dao.admit_v4_memory(
            tenant_id="tenant-evidence",
            workspace_id="workspace-evidence",
            dataset_id="dataset-evidence",
            agent_id="agent-evidence",
            session_id="session-evidence",
            document_id="document-legal-1",
            revision_id="revision-legal-1",
            chunk_id="chunk-legal-1",
            title="Çok Paragraflı Kanun Hükmü",
            content_payload=source,
            source_ref="law://example/article-1",
            evidence_span="",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={"jurisdiction": "TR"},
            embedding_provider="local-test",
            embedding_model="deterministic",
            embedding_version="v1",
            embedding_dimension=3,
            policy=config.queue_admission_policy,
            validation_mode=0,
        )
        mutation_id = admission["response"]["mutation_id"]
        assert await dao.record_mutation_extraction("agent-evidence", mutation_id, [])
        assert await dao.set_mutation_state("agent-evidence", mutation_id, "VALIDATED")
        projected = await process_projection_outbox_once(
            dao, worker_id="evidence-projector", limit=1
        )
        assert projected["completed"] == 1

        async with engine.transaction() as db:
            async with db.execute(
                "SELECT assertion_id, document_id, revision_id, chunk_id, evidence_span "
                "FROM v4_assertions WHERE mutation_id = ?",
                (mutation_id,),
            ) as cursor:
                assertion = await cursor.fetchone()
            async with db.execute(
                "SELECT content_payload FROM source_chunks WHERE tenant_id = ?",
                ("tenant-evidence",),
            ) as cursor:
                source_chunk = await cursor.fetchone()
            assert assertion is not None
            assert source_chunk is not None and source_chunk[0] == source
            assert assertion[4] == source

            for lane in ("VECTOR", "GRAPH"):
                await db.execute(
                    "UPDATE projection_outbox SET state = 'COMPLETED' "
                    "WHERE mutation_id = ? AND projection_name = ?",
                    (mutation_id, lane),
                )
                await MemoryDAO._advance_mutation_projection_state(db, mutation_id)
            await db.commit()

        context = await ContextBuilder(dao).build_context(
            tenant_id="tenant-evidence",
            agent_id="agent-evidence",
            dataset_ids=["dataset-evidence"],
            query="Genel başlangıç kuralı",
            token_budget=2048,
        )
        assert later_rule in context["formatted_context"]
        visible = context["canonical_memories"][0]
        assert visible["assertion_id"] == assertion[0]
        assert visible["document_id"] == "document-legal-1"
        assert visible["source_chunk_id"] == "chunk-legal-1"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_budget_packing_considers_small_rank_three_candidate(monkeypatch) -> None:
    """Two verbose early candidates must not starve a fitting later candidate."""
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
    candidates = [
        _candidate(1, evidence=("A " * 350) + "RANK_ONE"),
        _candidate(2, evidence=("B " * 200) + "RANK_TWO"),
        _candidate(3, evidence=("C " * 10) + "ANSWER_RANK_THREE"),
    ]
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = candidates

    context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=2048,
    )

    assert context["actual_token_count"] <= 2048
    assert "RANK_ONE" in context["formatted_context"]
    assert "ANSWER_RANK_THREE" in context["formatted_context"]
    assert [memory["candidate_id"] for memory in context["canonical_memories"]] == [
        "candidate-1",
        "candidate-3",
    ]


@pytest.mark.asyncio
async def test_all_fitting_evidence_stays_ranked_and_mixed_provenance_survives(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
    candidates = [
        _candidate(1, evidence="First complete rule."),
        _candidate(2, evidence="Second complete rule."),
        _candidate(3, evidence="Third complete rule."),
    ]
    candidates[0]["retrieval_provenance"]["origins"] = ["vector", "bm25"]
    candidates[1]["retrieval_provenance"]["origins"] = ["assertion"]
    candidates[2]["retrieval_provenance"]["origins"] = ["graph"]
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = candidates

    context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=2048,
    )

    assert [memory["candidate_id"] for memory in context["canonical_memories"]] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ]
    assert [
        memory["retrieval_provenance"]["origins"]
        for memory in context["canonical_memories"]
    ] == [["vector", "bm25"], ["assertion"], ["graph"]]


@pytest.mark.asyncio
async def test_duplicate_evidence_identity_does_not_consume_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
    duplicate = _candidate(1, evidence=("duplicate " * 40) + "DUPLICATE")
    later = _candidate(2, evidence="DISTINCT_LATER_EVIDENCE")
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = [duplicate, duplicate.copy(), later]

    context = await ContextBuilder(dao).build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=2048,
    )

    assert context["formatted_context"].count("DUPLICATE") == 1
    assert "DISTINCT_LATER_EVIDENCE" in context["formatted_context"]
    assert context["context_diagnostics"]["duplicate_candidate_count"] == 1


@pytest.mark.asyncio
async def test_exact_budget_boundary_and_one_token_over_are_deterministic(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mesa_memory.context_builder._count_tokens",
        lambda text: len(text.encode("utf-8")),
    )
    candidate = _candidate(1, evidence="Complete boundary evidence.")
    dao = AsyncMock()
    dao.get_recent_logs.return_value = []
    dao.search_v4_memory.return_value = [candidate]
    builder = ContextBuilder(dao)

    baseline = await builder.build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=10_000,
    )
    exact_budget = baseline["actual_token_count"]
    exact = await builder.build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=exact_budget,
    )
    one_over = await builder.build_context(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="target",
        token_budget=exact_budget - 1,
    )

    assert exact["actual_token_count"] == exact_budget
    assert exact["canonical_memories"][0]["candidate_id"] == "candidate-1"
    assert one_over["actual_token_count"] <= exact_budget - 1
    assert one_over["canonical_memories"] == []
    assert one_over["context_status"] == "VALID_EVIDENCE_EXCEEDS_CONTEXT_BUDGET"
