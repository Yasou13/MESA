"""Single-owner V4 projection outbox consumer.

The worker is intentionally called from the combined runtime, never from a
separate process: SQLite is the ownership ledger for all three stores.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_ProjectionWorker")

_LANE_ORDER = {"SQL": 0, "VECTOR": 1, "GRAPH": 2}


class PermanentProjectionError(ValueError):
    """A ledger payload cannot be projected by retrying it."""


class ProjectionLeaseLostError(RuntimeError):
    """The projector no longer owns its fenced outbox claim."""


def _triplets(record: dict[str, Any]) -> list[dict[str, Any]]:
    value = record.get("projection_triplets")
    if not isinstance(value, list):
        raise PermanentProjectionError("missing durable projection extraction")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise PermanentProjectionError("invalid durable projection extraction")
        try:
            triplet = {
                "head": str(item["head"]),
                "relation": str(item["relation"]),
                "tail": str(item["tail"]) if item.get("tail") else None,
                "literal_value": (
                    str(item["literal_value"])
                    if item.get("literal_value") is not None
                    else None
                ),
                "confidence": float(item.get("confidence", 1.0)),
            }
        except KeyError as exc:
            raise PermanentProjectionError("incomplete durable projection extraction") from exc
        if (
            not triplet["head"]
            or not triplet["relation"]
            or (triplet["tail"] is None) == (triplet["literal_value"] is None)
        ):
            raise PermanentProjectionError("blank durable projection extraction")
        result.append(triplet)
    return result


async def _apply_projection(dao: MemoryDAO, projection: dict[str, Any]) -> None:
    mutation = await dao.get_projection_mutation(str(projection["mutation_id"]))
    if mutation is None:
        raise PermanentProjectionError("mutation no longer exists")
    if mutation["state"] not in {
        "VALIDATED",
        "SQL_APPLIED",
        "VECTOR_APPLIED",
        "GRAPH_APPLIED",
        "RETRY_PENDING",
    }:
        raise PermanentProjectionError(f"mutation is not projectable: {mutation['state']}")
    triplets = _triplets(mutation)
    entities = sorted(
        {
            entity
            for triplet in triplets
            for entity in (triplet["head"], triplet.get("tail"))
            if entity
        }
    )
    lane = projection["projection_name"]
    if lane == "SQL":
        for entity in entities:
            await dao.project_v4_sql_entity(mutation=mutation, entity_name=entity)
    elif lane == "VECTOR":
        for entity in entities:
            await dao.project_v4_vector_entity(mutation=mutation, entity_name=entity)
    elif lane == "GRAPH":
        for triplet in triplets:
            await dao.project_v4_graph_triplet(mutation=mutation, triplet=triplet)
    else:
        raise PermanentProjectionError(f"unknown projection lane: {lane}")


async def _apply_with_lease_heartbeat(dao: MemoryDAO, projection: dict[str, Any], worker_id: str) -> None:
    """Keep ownership alive while a model/vector/graph call is in progress."""
    task = asyncio.create_task(_apply_projection(dao, projection))
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=60)
        except TimeoutError:
            renewed = await dao.renew_projection_outbox_lease(
                str(projection["projection_id"]),
                worker_id=worker_id,
                claim_token=str(projection["claim_token"]),
            )
            if not renewed:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise ProjectionLeaseLostError("projection outbox lease ownership was lost")
    await task


async def process_projection_outbox_once(
    dao: MemoryDAO, *, worker_id: str = "combined-runtime", limit: int = 1
) -> dict[str, int]:
    """Claim and apply a bounded set of fenced V4 projection lanes."""
    claimed = await dao.claim_projection_outbox(worker_id=worker_id, limit=limit)
    result = {"claimed": len(claimed), "completed": 0, "retry_pending": 0, "dead_letter": 0}
    for projection in sorted(claimed, key=lambda item: _LANE_ORDER.get(item["projection_name"], 99)):
        projection_id = str(projection["projection_id"])
        try:
            await _apply_with_lease_heartbeat(dao, projection, worker_id)
            completed = await dao.complete_projection_outbox(
                projection_id,
                worker_id=worker_id,
                claim_token=str(projection["claim_token"]),
                outcome="APPLIED",
            )
            result["completed"] += int(completed)
        except PermanentProjectionError as exc:
            changed = await dao.fail_projection_outbox(
                projection_id,
                worker_id=worker_id,
                claim_token=str(projection["claim_token"]),
                error_class=type(exc).__name__,
                retryable=False,
            )
            result["dead_letter"] += int(changed)
            logger.warning("V4_PROJECTION_PERMANENT_FAILURE | projection_id=%s error=%s", projection_id, exc)
        except Exception as exc:
            changed = await dao.fail_projection_outbox(
                projection_id,
                worker_id=worker_id,
                claim_token=str(projection["claim_token"]),
                error_class=type(exc).__name__,
                retryable=True,
            )
            if changed:
                mutation = await dao.get_projection_mutation(
                    str(projection["mutation_id"])
                )
                if mutation is not None and mutation["state"] == "DEAD_LETTER":
                    result["dead_letter"] += 1
                else:
                    result["retry_pending"] += 1
            logger.warning("V4_PROJECTION_RETRYABLE_FAILURE | projection_id=%s error=%s", projection_id, exc)
    return result


async def process_artifact_cleanup_once(
    dao: MemoryDAO, *, worker_id: str = "combined-runtime", limit: int = 1
) -> dict[str, int]:
    """Apply fenced rollback cleanup without touching shared artifacts."""
    claimed = await dao.claim_artifact_cleanup(worker_id=worker_id, limit=limit)
    result = {"claimed": len(claimed), "completed": 0, "retry_pending": 0, "blocked": 0}
    for cleanup in claimed:
        try:
            await dao.apply_artifact_cleanup(cleanup)
        except Exception as exc:
            changed = await dao.finish_artifact_cleanup(
                str(cleanup["cleanup_id"]),
                worker_id=worker_id,
                claim_token=str(cleanup["claim_token"]),
                error_class=type(exc).__name__,
            )
            if changed:
                pipeline = await dao.get_pipeline_run(
                    str(cleanup["pipeline_run_id"])
                )
                if pipeline and pipeline["state"] == "BLOCKED":
                    result["blocked"] += 1
                else:
                    result["retry_pending"] += 1
            logger.warning(
                "V4_CLEANUP_FAILURE | cleanup_id=%s error=%s",
                cleanup["cleanup_id"],
                exc,
            )
        else:
            completed = await dao.finish_artifact_cleanup(
                str(cleanup["cleanup_id"]),
                worker_id=worker_id,
                claim_token=str(cleanup["claim_token"]),
            )
            result["completed"] += int(completed)
    return result
