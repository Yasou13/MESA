# ADR 0003: Adaptive LLM Routing with Telemetry

## Status
Accepted

## Context
Running a Dual-LLM (Tier-3) consensus gate for every memory node extraction is financially unsustainable and induces high latency. However, routing everything blindly to a smaller model exposes the system to silent hallucinations and schema failures, threatening data integrity.

## Decision
We implemented an **AdaptiveRouter**:
1. **Primary Pass:** Validations default to the smaller, cheaper model.
2. **Confidence Proxy:** We simulate Temperature Scaling to calculate a `confidence_score` (Expected Calibration Error minimization).
3. **Dynamic Thresholding:** If `confidence_score < T_route`, the system falls back to the Dual-LLM gate. The `T_route` is dynamically penalized or decayed based on a 60-second rolling window error rate via `MemoryDAO`.
4. **Audit Sampling:** A random 5% of "confident" small-model decisions are secretly routed to the Dual-LLM for an audit. Discrepancies are logged as hallucinations to the `routing_telemetry` table.
5. **Schema Fallback:** Try/except blocks prevent `Tier3ValidationError` crashes from polluting the consolidation loop, explicitly routing broken schemas to the Dual-LLM.

## Consequences
- **Positive:** Massive reduction in LLM inference costs and latency.
- **Positive:** System auto-tunes itself, penalizing hallucination bursts without human intervention.
- **Negative:** Increased statefulness (cache timers) and telemetry overhead on the DAO.
