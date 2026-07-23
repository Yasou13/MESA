"""v4 full-cognitive ingestion and single-owner runtime contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mesa_memory.api.server import _consume_combined_durable_work_once
from mesa_memory.config import config
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_projection_outbox_once


def test_memory_candidate_is_retry_stable_and_preserves_payload() -> None:
    first = MemoryCandidate.from_raw_log(
        raw_log_id=41,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="The exact text passed to Tier-3.",
        metadata={"source_ref": "test"},
    )
    second = MemoryCandidate.from_raw_log(
        raw_log_id=41,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="The exact text passed to Tier-3.",
    )

    record = first.as_consolidation_record()
    assert first.candidate_id == second.candidate_id
    assert first.mutation_id == second.mutation_id
    assert record["cmb_id"] == first.candidate_id
    assert record["content_payload"] == "The exact text passed to Tier-3."
    assert record["tier3_deferred"] is True


@pytest.mark.asyncio
async def test_combined_owner_consumes_one_dispatch_and_pending_finalization() -> None:
    dispatch = {
        "queue_record_id": "queue-1",
        "payload_reference": 9,
        "agent_id": "tenant-a",
        "claim_token": "fence-1",
    }
    dao = SimpleNamespace(
        claim_dispatch_queue=AsyncMock(return_value=[dispatch]),
        get_raw_log=AsyncMock(return_value={"status": "processed"}),
        complete_dispatch_queue=AsyncMock(return_value=True),
        list_pending_session_finalizations=AsyncMock(
            return_value=[{"agent_id": "tenant-a", "session_id": "session-a"}]
        ),
    )
    loop = SimpleNamespace()

    with (
        patch("mesa_memory.api.server.process_cold_path", new=AsyncMock()) as cold_path,
        patch(
            "mesa_memory.api.server.process_session_finalization", new=AsyncMock()
        ) as finalizer,
    ):
        result = await _consume_combined_durable_work_once(
            dao, consolidation_loop=loop, model_processing_enabled=True
        )

    cold_path.assert_awaited_once_with(
        9,
        "tenant-a",
        dao,
        consolidation_loop=loop,
        model_processing_enabled=True,
        require_tier3_validation=True,
        retry_on_failure=True,
    )
    dao.complete_dispatch_queue.assert_awaited_once_with(
        "queue-1",
        worker_id="combined-runtime",
        claim_token="fence-1",
        outcome="processed",
        side_effect_verified=True,
    )
    finalizer.assert_awaited_once_with("tenant-a", "session-a", dao, loop)
    assert result == {
        "dispatches": 1,
        "finalizations": 1,
        "projections": 0,
        "cleanup": 0,
    }


@pytest.mark.asyncio
async def test_unverified_dispatch_moves_to_durable_dlq_after_five_attempts(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "dispatch.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        admission = await dao.admit_raw_log(
            "tenant-a",
            {"agent_id": "tenant-a", "session_id": "session-a", "content": "retry"},
            policy=config.queue_admission_policy,
        )
        queue_id = admission["queue_record_id"]
        for _ in range(5):
            claimed = await dao.claim_dispatch_queue(worker_id="worker-a", limit=1)
            assert len(claimed) == 1
            assert claimed[0]["queue_record_id"] == queue_id
            assert not await dao.complete_dispatch_queue(
                queue_id,
                worker_id="worker-a",
                claim_token=claimed[0]["claim_token"],
                outcome="RetryableFailure",
                side_effect_verified=False,
            )
        async with engine.connection() as db:
            async with db.execute(
                "SELECT state, last_error_class FROM dispatch_queue WHERE queue_record_id = ?",
                (queue_id,),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert row["state"] == "DEAD_LETTER"
        assert row["last_error_class"] == "RetryableFailure"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_mutation_ledger_is_idempotent_and_creates_projection_outbox(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "ledger.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=77,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="canonical payload",
        metadata={"source_ref": "fixture"},
    ).as_consolidation_record()
    try:
        first = await dao.record_mutation(candidate, raw_log_id=77)
        second = await dao.record_mutation(candidate, raw_log_id=77)
        assert first["mutation_id"] == second["mutation_id"] == candidate["mutation_id"]
        assert first["state"] == "RECEIVED"
        async with engine.connection() as db:
            async with db.execute(
                "SELECT projection_name FROM projection_outbox WHERE mutation_id = ? ORDER BY projection_name",
                (candidate["mutation_id"],),
            ) as cursor:
                projections = [row[0] for row in await cursor.fetchall()]
        assert projections == ["GRAPH", "SQL", "VECTOR"]
        # Tier-3 rejection cannot race a projector: lanes begin blocked until
        # the exact validated extraction is durably recorded.
        assert await dao.claim_projection_outbox(worker_id="projector-a", limit=1) == []
        assert await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [{"head": "Alice", "relation": "KNOWS", "tail": "Bob"}],
        )
        assert await dao.set_mutation_state(
            "tenant-a", candidate["mutation_id"], "VALIDATED"
        )
        claimed = await dao.claim_projection_outbox(worker_id="projector-a", limit=1)
        assert len(claimed) == 1
        assert await dao.complete_projection_outbox(
            claimed[0]["projection_id"],
            worker_id="projector-a",
            claim_token=claimed[0]["claim_token"],
            outcome="APPLIED",
        )
        stored = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert stored is not None and stored["state"] == "SQL_APPLIED"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_outbox_projects_each_lane_then_commits_mutation(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "outbox.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=91,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="canonical payload",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(candidate, raw_log_id=91)
        await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [{"head": "Alice", "relation": "KNOWS", "tail": "Bob"}],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")
        with (
            patch.object(MemoryDAO, "project_v4_sql_entity", new=AsyncMock(return_value="sql")),
            patch.object(MemoryDAO, "project_v4_vector_entity", new=AsyncMock(return_value="vector")),
            patch.object(MemoryDAO, "project_v4_graph_triplet", new=AsyncMock(return_value="assertion")),
        ):
            result = {"claimed": 0, "completed": 0, "retry_pending": 0, "dead_letter": 0}
            for _ in range(3):
                single = await process_projection_outbox_once(dao, worker_id="projector-a")
                for key in result:
                    result[key] += single[key]
        assert result == {"claimed": 3, "completed": 3, "retry_pending": 0, "dead_letter": 0}
        stored = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert stored is not None and stored["state"] == "COMMITTED"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_rejected_mutation_cancels_all_blocked_projection_lanes(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "rejected.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=92,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="rejected payload",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(candidate, raw_log_id=92)
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "REJECTED")
        async with engine.connection() as db:
            async with db.execute(
                "SELECT state FROM projection_outbox WHERE mutation_id = ?",
                (candidate["mutation_id"],),
            ) as cursor:
                states = {row[0] for row in await cursor.fetchall()}
        assert states == {"CANCELLED"}
        assert await dao.claim_projection_outbox(worker_id="projector-a", limit=3) == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_projection_retry_is_fenced_and_moves_to_dlq_after_limit(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "projection-dlq.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=93,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="retry payload",
    ).as_consolidation_record()
    try:
        await dao.record_mutation(candidate, raw_log_id=93)
        await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [{"head": "Alice", "relation": "KNOWS", "tail": "Bob"}],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")
        with patch.object(
            MemoryDAO, "project_v4_sql_entity", new=AsyncMock(side_effect=RuntimeError("transient"))
        ):
            outcomes = [
                await process_projection_outbox_once(dao, worker_id="projector-a")
                for _ in range(5)
            ]

        assert [item["retry_pending"] for item in outcomes] == [1, 1, 1, 1, 0]
        assert outcomes[-1]["dead_letter"] == 1
        mutation = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert mutation is not None and mutation["state"] == "DEAD_LETTER"
        async with engine.connection() as db:
            async with db.execute(
                "SELECT projection_name, state FROM projection_outbox WHERE mutation_id = ? ORDER BY projection_name",
                (candidate["mutation_id"],),
            ) as cursor:
                lanes = {row[0]: row[1] for row in await cursor.fetchall()}
        assert lanes == {"GRAPH": "PENDING", "SQL": "DEAD_LETTER", "VECTOR": "PENDING"}
    finally:
        await engine.close()
