# Modül 8: Consolidation — Handoff Document

## Import Paths

```python
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.lock import calculate_composite_similarity, validate_extraction_pair
```

## Exported Components

| Component | Type | Description |
|---|---|---|
| `ConsolidationLoop` | Class | Async batch processor with dual-prompt cross-validation and three-path divergence handling. |
| `calculate_composite_similarity` | Function | Composite triplet similarity with directionality alignment (head/tail swap detection). |
| `validate_extraction_pair` | Function | Jaccard-based entity/relation set comparison for batch-level validation. |

## Verified Thresholds

| Metric | Threshold | Status |
|---|---|---|
| Entity Similarity | `≥ 0.80` | ✅ Verified |
| Relation Similarity | `≥ 0.70` | ✅ Verified |

## Logic Summary

Asynchronous batch processing (N=20) with cross-validation alignment and three-path divergence policy:

| Path | Condition | Action |
|---|---|---|
| Normal | `sim ≥ 0.70` | Write to graph with `weight = 1.0` |
| Path 1 | `0.3 ≤ sim < 0.70` | Write intersection with `weight = 0.5` |
| Path 2 | `sim < 0.3` + Hub node (degree ≥ 5) | Queue for human review |
| Path 3 | `sim < 0.3` + Peripheral node | Silent discard + log |

## Architectural Constraint

> **CRITICAL: The Consolidation loop is an idle-time background process. Do not block the main event loop. All writes to the graph during consolidation must use the isolation weights (1.0 or 0.5) defined in the divergence policy.**
