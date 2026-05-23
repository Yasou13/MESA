# ADR 0002: Pivot from RRF to Alpha-Reranking

## Status
Accepted

## Context
Our initial hybrid retrieval relied on Reciprocal Rank Fusion (RRF). However, grid-search telemetry proved that RRF was mathematically incompatible with our asymmetric data distributions (dense vectors vs. sparse graph/lexical matches). The RRF logic consistently degraded the high-fidelity vector baseline and suffered from an O(N) latency bottleneck due to evaluating the entire database or an artificially restricted Top-50 list.

## Decision
We pivoted to a Score-Based Bonus system (**Alpha-Reranking**):
1. **Formula:** `Final Score = S_vec + (alpha * S_graph_norm) + (beta * S_lex_norm)`
2. **Union Set Candidate Pooling:** Retrieve Top-100 candidates from Vector, Lexical, and Graph, merging them into a unique set (max 300 items) to guarantee O(N) latency optimization and outlier inclusion.
3. **Deterministic Normalization:** FTS5 scores are scaled by an empirical constant (10.0), and Graph (PPR/overlap) scores are bounded by token maximums or a scaling factor, capping at 1.0 to ensure hyperparameters remain stable.

## Consequences
- **Positive:** Mathematically sound bounding. Retrieval performance can no longer fall below the pure-vector baseline.
- **Positive:** Latency is strictly bounded via the Union Set restriction.
- **Negative:** Requires hyperparameter grid searches (`alpha`, `beta`) for optimization against specific domains.
