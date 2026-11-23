"""Single-owner V4 projection outbox consumer.

The worker is intentionally called from the combined runtime, never from a
separate process: SQLite is the ownership ledger for SQL and LanceDB stores.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from typing import Any

from mesa_storage.dao import MemoryDAO, classify_graph_object

logger = logging.getLogger("MESA_ProjectionWorker")

_LANE_ORDER = {"SQL": 0, "VECTOR": 1}
_VECTOR_WRITE_BATCH_SIZE = 100
_PROJECTABLE_MUTATION_STATES = {
    "VALIDATED",
    "SQL_APPLIED",
    "VECTOR_APPLIED",
    "RETRY_PENDING",
    "COMMITTED",
}


class PermanentProjectionError(ValueError):
    """A ledger payload cannot be projected by retrying it."""


def _normalized_confidence(value: Any) -> float:
    """Normalize the optional extraction confidence at the projection boundary."""
    if value is None:
        return 1.0
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise PermanentProjectionError("projection confidence is not numeric") from exc
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise PermanentProjectionError("projection confidence is outside [0, 1]")
    return confidence


def _triplets(record: dict[str, Any]) -> list[dict[str, Any]]:
    value = record.get("projection_triplets")
    if not isinstance(value, list) or len(value) == 0:
        content = record.get("content_payload") or ""
        doc_id = str(record.get("document_id") or record.get("title") or "Belge")
        chunk_id = str(record.get("chunk_id") or doc_id)
        legal_identity = record.get("legal_identity")
        # This fallback assertion represents the complete source chunk because
        # no finer-grained extracted fact exists.  A bounded preview here would
        # become the canonical assertion evidence consumed by ContextBuilder
        # and irreversibly hide later clauses even though the source chunk is
        # still durable in SQLite.
        evidence = str(record.get("evidence_span") or content)
        # Automatic document identities affect typed retrieval only. Preserve
        # the original document/chunk graph and lexical subject for marked
        # document chunks. Explicit identities retain main's article fallback.
        if (
            isinstance(legal_identity, dict)
            and record.get("document_chunk") is not True
        ):
            statute_code = str(legal_identity["statute_code"])
            article_id = str(legal_identity["article_id"])
            head = f"{statute_code} m.{article_id}"
            relation = "hükmü"
            tail = None
            literal_value = str(content or evidence)
        else:
            head = doc_id
            relation = "madde"
            tail = chunk_id
            literal_value = None
        return [
            {
                "head": head,
                "relation": relation,
                "tail": tail,
                "literal_value": literal_value,
                "confidence": 1.0,
                "fact_text": content if content else doc_id,
                "source_span": evidence,
                "valid_from": None,
                "valid_to": None,
                "supersedes": None,
                "metadata": record.get("metadata", {}),
            }
        ]
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise PermanentProjectionError("invalid durable projection extraction")
        try:
            triplet: dict[str, Any] = {
                "head": str(item["head"]),
                "relation": str(item["relation"]),
                "tail": str(item["tail"]) if item.get("tail") else None,
                "literal_value": (
                    str(item["literal_value"])
                    if item.get("literal_value") is not None
                    else None
                ),
                "confidence": _normalized_confidence(item.get("confidence", 1.0)),
                "fact_text": str(item.get("fact_text") or ""),
                "valid_from": item.get("valid_from"),
                "valid_to": item.get("valid_to"),
                "source_span": item.get("source_span"),
                "supersedes": item.get("supersedes"),
                "object_type": item.get("object_type") or item.get("tail_type"),
                "metadata": (
                    item.get("metadata")
                    if isinstance(item.get("metadata"), dict)
                    else {}
                ),
            }
        except KeyError as exc:
            raise PermanentProjectionError(
                "incomplete durable projection extraction"
            ) from exc
        if (
            not triplet["head"]
            or not triplet["relation"]
            or (triplet["tail"] is None) == (triplet["literal_value"] is None)
        ):
            raise PermanentProjectionError("blank durable projection extraction")
        # Resolve semantics before the SQL lane creates object entities.
        triplet["tail"], triplet["literal_value"], triplet["object_type"] = (
            classify_graph_object(
                tail=triplet["tail"],
                literal_value=triplet["literal_value"],
                object_type=triplet["object_type"],
            )
        )
        result.append(triplet)
    return result


def _prepare_sql_projection(
    mutation: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    triplets = _triplets(mutation)
    entities = sorted(
        {
            entity
            for triplet in triplets
            for entity in (triplet["head"], triplet.get("tail"))
            if entity
        }
    )
    return (entities, triplets)


async def _load_projectable_mutation(
    dao: MemoryDAO, projection: dict[str, Any]
) -> dict[str, Any]:
    mutation = await dao.get_projection_mutation(str(projection["mutation_id"]))
    if mutation is None:
        raise PermanentProjectionError("mutation no longer exists")
    if mutation["state"] not in _PROJECTABLE_MUTATION_STATES:
        raise PermanentProjectionError(
            f"mutation is not projectable: {mutation['state']}"
        )
    return mutation


async def _apply_projection(dao: MemoryDAO, projection: dict[str, Any]) -> None:
    mutation = await _load_projectable_mutation(dao, projection)
    entities, triplets = _prepare_sql_projection(mutation)
    lane = projection["projection_name"]
    if lane == "SQL":
        for entity in entities:
            await dao.project_v4_sql_entity(mutation=mutation, entity_name=entity)
        for triplet in triplets:
            await dao.project_v4_sql_assertion(mutation=mutation, triplet=triplet)
    elif lane == "VECTOR":
        assertions = await dao.list_v4_assertions_for_mutation(
            str(mutation["mutation_id"])
        )
        for assertion in assertions:
            await dao.project_v4_vector_assertion(
                mutation=mutation, assertion=assertion
            )
    else:
        raise PermanentProjectionError(f"unknown projection lane: {lane}")


async def _apply_with_lease_heartbeat(
    dao: MemoryDAO, projection: dict[str, Any], worker_id: str
) -> None:
    """Apply the projection directly."""
    await _apply_projection(dao, projection)


async def _try_apply_sql_batch(
    dao: MemoryDAO, sql_items: list[dict[str, Any]]
) -> bool:
    try:
        mutations_by_id = await dao.get_projection_mutations_batch(
            [str(p["mutation_id"]) for p in sql_items]
        )
        batch_inputs: list[tuple[dict[str, Any], list[str], list[dict[str, Any]]]] = []
        for p in sql_items:
            mutation = mutations_by_id.get(str(p["mutation_id"]))
            if (
                mutation is None
                or mutation["state"] not in _PROJECTABLE_MUTATION_STATES
            ):
                return False
            entities, triplets = _prepare_sql_projection(mutation)
            batch_inputs.append((mutation, entities, triplets))
        await dao.apply_v4_sql_projections_batch(batch_inputs)
        return True
    except Exception:
        if len(sql_items) == 1:
            raise
        return False


async def process_projection_outbox_once(
    dao: MemoryDAO, *, worker_id: str = "combined-runtime", limit: int = 50
) -> dict[str, int]:
    """Claim and apply a bounded set of fenced V4 projection lanes."""
    claimed = await dao.claim_projection_outbox(worker_id=worker_id, limit=limit)
    result = {
        "claimed": len(claimed),
        "completed": 0,
        "retry_pending": 0,
        "dead_letter": 0,
    }

    skipped_projections: list[dict[str, Any]] = []
    successful_projections: list[dict[str, Any]] = []

    async def _record_projection_failure(
        projection: dict[str, Any], exc: Exception
    ) -> None:
        projection_id = str(projection["projection_id"])
        if isinstance(exc, PermanentProjectionError):
            changed = await dao.fail_projection_outbox(
                projection_id,
                worker_id=worker_id,
                claim_token=str(projection["claim_token"]),
                error_class=type(exc).__name__,
                retryable=False,
            )
            if changed:
                result["dead_letter"] += 1
            logger.warning(
                "V4_PROJECTION_PERMANENT_FAILURE | projection_id=%s error=%s",
                projection_id,
                exc,
            )
            return
        changed = await dao.fail_projection_outbox(
            projection_id,
            worker_id=worker_id,
            claim_token=str(projection["claim_token"]),
            error_class=type(exc).__name__,
            retryable=True,
        )
        if changed:
            if int(projection["attempt_count"]) >= int(projection["retry_limit"]):
                result["dead_letter"] += 1
            else:
                result["retry_pending"] += 1
        logger.warning(
            "V4_PROJECTION_RETRYABLE_FAILURE | projection_id=%s error=%s",
            projection_id,
            exc,
        )

    async def _handle_one(projection: dict[str, Any]) -> None:
        try:
            if projection["projection_name"] == "VECTOR" and not dao.embedding_enabled:
                skipped_projections.append(projection)
                return
            await _apply_with_lease_heartbeat(dao, projection, worker_id)
            successful_projections.append(projection)
        except Exception as exc:
            await _record_projection_failure(projection, exc)

    sorted_projections = sorted(
        claimed, key=lambda item: _LANE_ORDER.get(item["projection_name"], 99)
    )
    active_by_lane: dict[str, list[dict[str, Any]]] = {}
    for p in sorted_projections:
        lane = str(p["projection_name"])
        if lane == "VECTOR" and not dao.embedding_enabled:
            skipped_projections.append(p)
        else:
            active_by_lane.setdefault(lane, []).append(p)

    for lane, items in active_by_lane.items():
        if lane == "SQL":
            try:
                if await _try_apply_sql_batch(dao, items):
                    successful_projections.extend(items)
                    continue
            except Exception as exc:
                await _record_projection_failure(items[0], exc)
                continue
        if (
            lane == "VECTOR"
            and len(items) > 1
            and callable(getattr(type(dao.vector_engine), "bulk_upsert", None))
        ):
            for start in range(0, len(items), _VECTOR_WRITE_BATCH_SIZE):
                group = items[start : start + _VECTOR_WRITE_BATCH_SIZE]
                pending: list[
                    tuple[dict[str, Any], dict[str, Any], Iterator[dict[str, Any]]]
                ] = []
                for projection in group:
                    try:
                        mutation = await _load_projectable_mutation(dao, projection)
                        _prepare_sql_projection(mutation)
                        assertions = await dao.list_v4_assertions_for_mutation(
                            str(mutation["mutation_id"])
                        )
                    except Exception as exc:
                        await _record_projection_failure(projection, exc)
                    else:
                        pending.append((projection, mutation, iter(assertions)))

                # Keep assertion order within each mutation. A failed assertion
                # ends work for that mutation without advancing its outbox row.
                while pending:
                    writes = []
                    writing = []
                    for entry in pending:
                        projection, mutation, remaining_assertions = entry
                        assertion = next(remaining_assertions, None)
                        if assertion is None:
                            successful_projections.append(projection)
                        else:
                            writes.append((mutation, assertion))
                            writing.append(entry)
                    pending = []
                    outcomes = await dao.project_v4_vector_assertions_batch(writes)
                    for entry, outcome in zip(writing, outcomes, strict=True):
                        if outcome is None:
                            pending.append(entry)
                        else:
                            await _record_projection_failure(entry[0], outcome)
            continue
        for p in items:
            await _handle_one(p)

    if skipped_projections:
        result["completed"] += await dao.complete_projection_outbox_batch(
            skipped_projections, worker_id=worker_id, outcome="SKIPPED"
        )
    if successful_projections:
        result["completed"] += await dao.complete_projection_outbox_batch(
            successful_projections, worker_id=worker_id
        )
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
                pipeline = await dao.get_pipeline_run(str(cleanup["pipeline_run_id"]))
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
