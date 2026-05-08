import logging
import json
import time
from enum import Enum
from collections import Counter, deque

from mesa_memory.config import config

class SystemState(str, Enum):
    ADMIT = "ADMIT"
    DISCARD = "DISCARD"
    UNCERTAIN = "UNCERTAIN"
    ERROR = "ERROR"


class MetricsRegistry:

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
            self.histograms[name] = deque(maxlen=config.histogram_max_size)
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

    def log_valence_decision(self, tier: int, decision: SystemState | str, justification: str, cost: dict):
        if isinstance(decision, str) and decision == "STORE":
            decision = SystemState.ADMIT
        elif isinstance(decision, str):
            try:
                decision = SystemState(decision)
            except ValueError:
                pass

        decision_val = decision.value if isinstance(decision, SystemState) else str(decision)

        entry = {
            "event": "valence_decision",
            "tier": tier,
            "decision": decision_val,
            "justification": justification,
            "cost": cost,
            "timestamp": time.time(),
        }
        self.logger.info(json.dumps(entry))
        self.metrics.inc(f"valence_tier_{tier}_hits")
        self.metrics.inc(f"valence_decision_{decision_val}")
        
        admitted = self.metrics.counters.get(f"valence_decision_{SystemState.ADMIT.value}", 0)
        total = admitted + self.metrics.counters.get(f"valence_decision_{SystemState.DISCARD.value}", 0)
        if total > 0:
            self.metrics.set("cmb_admission_rate", admitted / total)

    def log_consolidation_batch(self, batch_id: str, processed: int, divergences: int, writes: int, duration_ms: float):
        divergence_rate = divergences / processed if processed > 0 else 0.0
        entry = {
            "event": "consolidation_batch",
            "batch_id": batch_id,
            "processed": processed,
            "divergences": divergences,
            "divergence_rate": round(divergence_rate, 4),
            "writes": writes,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        self.logger.info(json.dumps(entry))
        self.metrics.observe("consolidation_batch_duration", duration_ms)
        self.metrics.inc("cross_validation_divergence", divergences)
        self.metrics.set("consolidation_divergence_rate", divergence_rate)

        if divergence_rate > config.metrics_divergence_threshold:
            self.logger.warning(
                "BAD_BATCH: batch %s divergence_rate=%.2f exceeds 50%% threshold "
                "(%d divergences / %d processed)",
                batch_id, divergence_rate, divergences, processed,
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
