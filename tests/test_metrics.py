from mesa_memory.observability.metrics import MetricsRegistry, ObservabilityLayer


def test_metrics_registry():
    registry = MetricsRegistry()
    registry.inc("test_counter")
    registry.set("test_gauge", 1.5)
    registry.observe("test_hist", 100)
    assert registry.counters["test_counter"] == 1
    assert registry.gauges["test_gauge"] == 1.5
    assert list(registry.histograms["test_hist"]) == [100]


def test_observability_layer():
    obs = ObservabilityLayer()
    obs.log_valence_decision(3, "STORE", "test", {"latency": 100, "tokens": 10})
    assert obs.metrics.counters["valence_tier_3_hits"] == 1
    assert obs.metrics.counters["valence_decision_ADMIT"] == 1


def test_health_status_bloat():
    obs = ObservabilityLayer()
    obs.metrics.set("cmb_admission_rate", 0.85)
    assert obs.get_health_status()["status"] == "BLOAT_WARNING"
