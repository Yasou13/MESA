"""WAVE-004 durable DLQ file-queue ownership contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mesa_memory.consolidation.loop import PersistentQueue


@pytest.mark.asyncio
async def test_dlq_claim_is_single_owner_and_ack_is_fenced(tmp_path: Path):
    queue = PersistentQueue(str(tmp_path / "dlq.jsonl"))
    await queue.aappend(
        {"cmb_id": "node-1", "agent_id": "tenant-a", "error": "secret-like failure"}
    )

    batches = await asyncio.gather(
        queue.aclaim(worker_id="worker-a", limit=10),
        queue.aclaim(worker_id="worker-b", limit=10),
    )
    claims = [item for batch in batches for item in batch]
    assert len(claims) == 1
    claim = claims[0]
    assert claim["agent_id"] == "tenant-a"
    assert claim["state"] == "PROCESSING"
    assert claim["error_summary"] == "failure recorded"
    assert not await queue.aack([claim], worker_id="other-worker")
    assert await queue.aack([claim], worker_id=claim["claimed_by"])
    assert await queue.alen() == 0


@pytest.mark.asyncio
async def test_dlq_expiry_reclaims_and_bounded_nack_keeps_poison_evidence(
    tmp_path: Path,
):
    path = tmp_path / "dlq.jsonl"
    queue = PersistentQueue(str(path))
    await queue.aappend(
        {"cmb_id": "node-2", "agent_id": "tenant-a", "error": "failure"}
    )
    _ = (await queue.aclaim(worker_id="worker-a"))[0]

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["lease_expires_at"] = "1970-01-01T00:00:00+00:00"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    replay = (await queue.aclaim(worker_id="worker-b"))[0]
    assert replay["claimed_by"] == "worker-b"

    for attempt in range(3):
        assert await queue.anack(
            [replay], worker_id="worker-b", error_type="RuntimeError"
        )
        if attempt < 2:
            replay = (await queue.aclaim(worker_id="worker-b"))[0]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["state"] == "BLOCKED"
    assert rows[0]["attempt_count"] == 4
    assert rows[0]["last_error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_dlq_quarantines_malformed_tail_and_rejects_duplicate_queue_id(
    tmp_path: Path,
):
    path = tmp_path / "dlq.jsonl"
    queue = PersistentQueue(str(path))
    await queue.aappend(
        {"queue_id": "valid-1", "cmb_id": "node-1", "agent_id": "tenant-a"}
    )
    with path.open("ab") as handle:
        handle.write(b'{"queue_id":"truncated"')

    assert await queue.alen() == 1
    quarantine = path.with_name(path.name + ".malformed.jsonl")
    event = json.loads(quarantine.read_text().strip())
    assert event["error_type"] == "JSONDecodeError"
    assert event["byte_length"] > 0
    assert [json.loads(line)["queue_id"] for line in path.read_text().splitlines()] == [
        "valid-1"
    ]

    with pytest.raises(ValueError, match="duplicate DLQ queue_id"):
        await queue.aappend(
            {"queue_id": "valid-1", "cmb_id": "node-duplicate", "agent_id": "tenant-a"}
        )


@pytest.mark.asyncio
async def test_dlq_write_boundary_hook_is_explicit_and_inactive_by_default(
    tmp_path: Path,
):
    observed: list[str] = []
    queue = PersistentQueue(
        str(tmp_path / "hooked.jsonl"), _test_crash_hook=observed.append
    )
    await queue.aappend({"queue_id": "hooked-1", "agent_id": "tenant-a"})
    assert observed == [
        "before_serialization",
        "after_serialization_before_file_open",
        "after_file_open_before_write",
        "before_write",
        "after_write_before_flush",
        "after_flush_before_fsync",
        "after_fsync_before_close",
        "after_close_before_rename",
        "after_rename_before_directory_fsync",
        "after_directory_fsync",
    ]
    unhooked = PersistentQueue(str(tmp_path / "unhooked.jsonl"))
    await unhooked.aappend({"queue_id": "normal-1", "agent_id": "tenant-a"})
    assert await unhooked.alen() == 1


@pytest.mark.asyncio
async def test_completion_receipt_is_durable_before_ack_and_restart_reconciles(
    tmp_path: Path,
):
    path = tmp_path / "receipted.jsonl"
    queue = PersistentQueue(str(path), require_completion_receipt=True)
    await queue.aappend(
        {"queue_id": "record-1", "cmb_id": "node-1", "agent_id": "tenant-a"}
    )
    claim = (await queue.aclaim(worker_id="worker-a", limit=1, lease_seconds=1))[0]

    assert not await queue.aack([claim], worker_id="worker-a")
    assert await queue.acomplete(
        claim, worker_id="worker-a", outcome="SUCCEEDED", side_effect_verified=True
    )
    receipt = await queue.acompletion_receipt("record-1")
    assert receipt is not None
    assert receipt["agent_id"] == "tenant-a"
    assert receipt["receipt_version"] == 1
    assert await queue.alen() == 0

    def crash_after_receipt(point: str) -> None:
        if point == "after_receipt_fsync_before_ack":
            raise RuntimeError("receipt-ack-crash")

    crash_path = tmp_path / "crash.jsonl"
    crashing = PersistentQueue(
        str(crash_path),
        require_completion_receipt=True,
        _test_crash_hook=crash_after_receipt,
    )
    await crashing.aappend(
        {"queue_id": "record-2", "cmb_id": "node-2", "agent_id": "tenant-a"}
    )
    crash_claim = (
        await crashing.aclaim(worker_id="worker-a", limit=1, lease_seconds=1)
    )[0]
    with pytest.raises(RuntimeError, match="receipt-ack-crash"):
        await crashing.acomplete(
            crash_claim, worker_id="worker-a", side_effect_verified=True
        )
    assert await crashing.acompletion_receipt("record-2") is not None

    record = json.loads(crash_path.read_text().strip())
    record["lease_expires_at"] = 0
    crash_path.write_text(json.dumps(record) + "\n")
    restarted = PersistentQueue(str(crash_path), require_completion_receipt=True)
    reclaimed = (await restarted.aclaim(worker_id="worker-b", limit=1))[0]
    assert await restarted.areconcile_receipted_claim(reclaimed, worker_id="worker-b")
    assert await restarted.alen() == 0
