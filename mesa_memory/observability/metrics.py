from prometheus_client import Counter as PromCounter
from prometheus_client import Gauge as PromGauge

from mesa_storage.rebuild_health import REBUILD_OPERATION_STATES

# ---------------------------------------------------------------------------
# Prometheus metrics — module-level singletons (registered once per process)
# ---------------------------------------------------------------------------
PROM_HTTP_REQUESTS = PromCounter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"]
)
PROM_QUEUE_BACKLOG = PromGauge("queue_backlog_size", "Current ingestion queue backlog")
PROM_RETRIEVAL_DEGRADED = PromCounter(
    "mesa_retrieval_degraded_total",
    "Hybrid retrieval requests completed with an unavailable source",
    ["source"],
)
PROM_V4_PROJECTION_BACKLOG = PromGauge(
    "mesa_v4_projection_backlog",
    "Pending, retrying or in-flight V4 projection lanes",
)
PROM_V4_PROJECTION_DLQ = PromGauge(
    "mesa_v4_projection_dead_letter",
    "V4 projection lanes in dead letter",
)
PROM_V4_STUCK_LEASES = PromGauge(
    "mesa_v4_projection_stuck_leases",
    "Expired in-flight V4 projection leases",
)
PROM_V4_CLEANUP_BACKLOG = PromGauge(
    "mesa_v4_cleanup_backlog",
    "Pending, retrying or in-flight V4 artifact cleanup work",
)
PROM_V4_CLEANUP_BLOCKED = PromGauge(
    "mesa_v4_cleanup_blocked",
    "Blocked V4 artifact cleanup work",
)
PROM_V4_ORPHAN_REGISTRY = PromGauge(
    "mesa_v4_orphan_registry",
    "Active V4 registry artifacts without an active source owner",
)
PROM_V4_SHARED_ARTIFACTS = PromGauge(
    "mesa_v4_shared_artifacts",
    "V4 physical artifacts with multiple active source owners",
)
PROM_V4_REBUILD_STATE = PromGauge(
    "mesa_v4_rebuild_operation_state",
    "Whether the latest durable projection rebuild is in this state",
    ["state"],
)
PROM_V4_REBUILD_DURATION = PromGauge(
    "mesa_v4_rebuild_duration_seconds",
    "Elapsed duration of the latest durable projection rebuild",
)
PROM_V4_REBUILD_PROGRESS_COMPLETED = PromGauge(
    "mesa_v4_rebuild_progress_completed",
    "Completed durable projection rebuild work units",
)
PROM_V4_REBUILD_PROGRESS_TOTAL = PromGauge(
    "mesa_v4_rebuild_progress_total",
    "Total durable projection rebuild work units",
)
PROM_V4_REBUILD_PARITY_MISSING = PromGauge(
    "mesa_v4_rebuild_parity_missing",
    "Missing artifacts reported by the latest bounded parity check",
)
PROM_V4_REBUILD_STAGING_BYTES = PromGauge(
    "mesa_v4_rebuild_staging_bytes",
    "Bytes retained in the latest rebuild staging generation",
)
PROM_V4_REBUILD_ROLLBACKS = PromGauge(
    "mesa_v4_rebuild_rollbacks_total",
    "Durable projection generation rollbacks recorded in SQLite",
)


def update_v4_health_metrics(health: dict) -> None:  # type: ignore[type-arg]
    """Publish the durable health snapshot without content/provenance labels."""
    projection = health.get("v4_projection") or {}
    ownership = health.get("v4_ownership") or {}
    rebuild = health.get("v4_rebuild") or {}
    PROM_V4_PROJECTION_BACKLOG.set(float(projection.get("backlog", 0)))
    PROM_V4_PROJECTION_DLQ.set(float(projection.get("dead_letter", 0)))
    PROM_V4_STUCK_LEASES.set(float(projection.get("stuck_claims", 0)))
    PROM_V4_CLEANUP_BACKLOG.set(float(ownership.get("cleanup_backlog", 0)))
    PROM_V4_CLEANUP_BLOCKED.set(float(ownership.get("cleanup_blocked", 0)))
    PROM_V4_ORPHAN_REGISTRY.set(float(ownership.get("orphan_registry", 0)))
    PROM_V4_SHARED_ARTIFACTS.set(float(ownership.get("shared_artifacts", 0)))
    state = str(rebuild.get("state", "IDLE"))
    if state not in REBUILD_OPERATION_STATES:
        state = "IDLE"
    for candidate in REBUILD_OPERATION_STATES:
        PROM_V4_REBUILD_STATE.labels(state=candidate).set(
            1.0 if candidate == state else 0.0
        )
    progress = rebuild.get("progress") or {}
    PROM_V4_REBUILD_DURATION.set(float(rebuild.get("duration_seconds", 0)))
    PROM_V4_REBUILD_PROGRESS_COMPLETED.set(float(progress.get("completed", 0)))
    PROM_V4_REBUILD_PROGRESS_TOTAL.set(float(progress.get("total", 0)))
    PROM_V4_REBUILD_PARITY_MISSING.set(float(rebuild.get("parity_missing", 0)))
    PROM_V4_REBUILD_STAGING_BYTES.set(float(rebuild.get("staging_bytes", 0)))
    PROM_V4_REBUILD_ROLLBACKS.set(float(rebuild.get("rollback_count", 0)))
