import logging
import json
import time
from collections import Counter, deque


class MetricsRegistry:
    HISTOGRAM_MAX_SIZE = 10000

    def __init__(self):
        self.counters = Counter()
        self.gauges = {}
        self.histograms = {}

    def inc(self, name: str, value: int = 1):
        self.counters[name] += value

    def set(self, name: str, value: float):
        self.gauges[name] = value

    def observe(self, name: str, value: float):
        if name not in self.histograms:
            self.histograms[name] = deque(maxlen=self.HISTOGRAM_MAX_SIZE)
        self.histograms[name].append(value)


class ObservabilityLayer:
    def __init__(self):
        self.logger = logging.getLogger("MESA_Observability")
        self.logger.setLevel(logging.DEBUG)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG)
            self.logger.addHandler(handler)
        self.metrics = MetricsRegistry()

    def log_valence_decision(self, tier: int, decision: str, justification: str, cost: dict):
        entry = {
            "event": "valence_decision",
            "tier": tier,
            "decision": decision,
            "justification": justification,
            "cost": cost,
            "timestamp": time.time(),
        }
        self.logger.info(json.dumps(entry))
        self.metrics.inc(f"valence_tier_{tier}_hits")
        self.metrics.inc(f"valence_decision_{decision}")
        admitted = self.metrics.counters.get("valence_decision_ADMIT", 0)
        discarded = self.metrics.counters.get("valence_decision_DISCARD", 1)
        self.metrics.set("cmb_admission_rate", admitted / (admitted + discarded))

    def log_consolidation_batch(self, batch_id: str, processed: int, divergences: int, writes: int, duration_ms: float):
        entry = {
            "event": "consolidation_batch",
            "batch_id": batch_id,
            "processed": processed,
            "divergences": divergences,
            "writes": writes,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        self.logger.info(json.dumps(entry))
        self.metrics.observe("consolidation_batch_duration", duration_ms)
        self.metrics.inc("cross_validation_divergence", divergences)

    def get_health_status(self) -> dict:
        admission_rate = self.metrics.gauges.get("cmb_admission_rate", 0.0)
        status = "BLOAT_WARNING" if admission_rate >= 0.8 else "HEALTHY"
        return {
            "status": status,
            "counters": dict(self.metrics.counters),
            "gauges": dict(self.metrics.gauges),
        }
