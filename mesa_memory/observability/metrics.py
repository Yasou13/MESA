from collections import Counter, deque
from enum import Enum

import structlog
from prometheus_client import Counter as PromCounter
from prometheus_client import Gauge as PromGauge
from prometheus_client import Histogram as PromHistogram

from mesa_memory.config import config


class SystemState(str, Enum):
    ADMIT = "ADMIT"
    DISCARD = "DISCARD"
    UNCERTAIN = "UNCERTAIN"
    ERROR = "ERROR"


class MetricsRegistry:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.counters: Counter[str] = Counter()
        self.gauges = {}
        self.histograms = {}

    def inc(self, name: str, value: int = 1):  # type: ignore[no-untyped-def]
        self.counters[name] += value

    def set(self, name: str, value: float):  # type: ignore[no-untyped-def]
        self.gauges[name] = value

    def observe(self, name: str, value: float):  # type: ignore[no-untyped-def]
        if name not in self.histograms:
            self.histograms[name] = deque(maxlen=config.histogram_max_size)
        self.histograms[name].append(value)


# ---------------------------------------------------------------------------
# Prometheus metrics — module-level singletons (registered once per process)
# ---------------------------------------------------------------------------
PROM_VALENCE_HITS = PromCounter(
    "mesa_valence_tier_hits_total", "Total hits per valence tier", ["tier"]
)
PROM_VALENCE_DECISIONS = PromCounter(
    "mesa_valence_decisions_total", "Total valence decisions", ["decision"]
)
PROM_CONSOLIDATION_DURATION = PromHistogram(
    "mesa_consolidation_duration_ms", "Consolidation batch duration in ms"
)
PROM_CROSS_VALIDATION_DIVERGENCE = PromCounter(
    "mesa_cross_validation_divergence_total",
    "Total cross-validation divergences",
)
PROM_ADMISSION_RATE = PromGauge("mesa_cmb_admission_rate", "CMB admission rate")
PROM_DIVERGENCE_RATE = PromGauge(
    "mesa_consolidation_divergence_rate", "Consolidation divergence rate"
)
PROM_SAGA_FAILURES = PromCounter("saga_failure_total", "Total dual-write saga failures")
PROM_HTTP_REQUESTS = PromCounter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"]
)
PROM_QUEUE_BACKLOG = PromGauge("queue_backlog_size", "Current ingestion queue backlog")
PROM_RETRIEVAL_DEGRADED = PromCounter(
    "mesa_retrieval_degraded_total",
    "Hybrid retrieval requests completed with an unavailable source",
    ["source"],
)


class ObservabilityLayer:
    def __init__(self):  # type: ignore[no-untyped-def]
        self.logger = structlog.get_logger("MESA_Observability")
        self.metrics = MetricsRegistry()

    def log_valence_decision(  # type: ignore[no-untyped-def]
        self, tier: int, decision: SystemState | str, justification: str, cost: dict
    ):
        if isinstance(decision, str) and decision == "STORE":
            decision = SystemState.ADMIT
        elif isinstance(decision, str):
            try:
                decision = SystemState(decision)
            except ValueError:
                pass

        decision_val = (
            decision.value if isinstance(decision, SystemState) else str(decision)
        )

        safe_cost = {
            key: value
            for key, value in cost.items()
            if key in {"latency", "latency_ms", "token_count", "tokens", "usd"}
        }
        self.logger.info(
            "valence_decision",
            tier=tier,
            decision=decision_val,
            justification_length=len(justification),
            cost=safe_cost,
        )
        self.metrics.inc(f"valence_tier_{tier}_hits")
        self.metrics.inc(f"valence_decision_{decision_val}")

        PROM_VALENCE_HITS.labels(tier=str(tier)).inc()
        PROM_VALENCE_DECISIONS.labels(decision=decision_val).inc()

        admitted = self.metrics.counters.get(
            f"valence_decision_{SystemState.ADMIT.value}", 0
        )
        total = admitted + self.metrics.counters.get(
            f"valence_decision_{SystemState.DISCARD.value}", 0
        )
        if total > 0:
            rate = admitted / total
            self.metrics.set("cmb_admission_rate", rate)
            PROM_ADMISSION_RATE.set(rate)

    def log_consolidation_batch(  # type: ignore[no-untyped-def]
        self,
        batch_id: str,
        processed: int,
        divergences: int,
        writes: int,
        duration_ms: float,
    ):
        divergence_rate = divergences / processed if processed > 0 else 0.0
        self.logger.info(
            "consolidation_batch",
            batch_id=batch_id,
            processed=processed,
            divergences=divergences,
            divergence_rate=round(divergence_rate, 4),
            writes=writes,
            duration_ms=duration_ms,
        )
        self.metrics.observe("consolidation_batch_duration", duration_ms)
        self.metrics.inc("cross_validation_divergence", divergences)
        self.metrics.set("consolidation_divergence_rate", divergence_rate)

        PROM_CONSOLIDATION_DURATION.observe(duration_ms)
        PROM_CROSS_VALIDATION_DIVERGENCE.inc(divergences)
        PROM_DIVERGENCE_RATE.set(divergence_rate)

        if divergence_rate > config.metrics_divergence_threshold:
            self.logger.warning(
                "BAD_BATCH",
                batch_id=batch_id,
                divergence_rate=round(divergence_rate, 4),
                divergences=divergences,
                processed=processed,
            )

    def get_health_status(self) -> dict:
        admission_rate = self.metrics.gauges.get("cmb_admission_rate", 0.0)
        divergence_rate = self.metrics.gauges.get("consolidation_divergence_rate", 0.0)
        if admission_rate >= config.metrics_admission_threshold:
            status = "BLOAT_WARNING"
        elif divergence_rate > config.metrics_divergence_threshold:
            status = "BAD_BATCH_WARNING"
        else:
            status = "HEALTHY"
        return {
            "status": status,
            "counters": dict(self.metrics.counters),
            "gauges": dict(self.metrics.gauges),
        }
