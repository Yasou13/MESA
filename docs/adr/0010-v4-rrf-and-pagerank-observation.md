# ADR 0010: True RRF and observation-only PageRank

- Status: Accepted
- Supersedes: ADR 0002 for v4 retrieval and ADR 0005 in full.

## Context

Score-weight blending mixed incomparable vector, lexical and graph score
spaces. Low PageRank centrality was also not evidence that a fact was false
and could incorrectly remove valid peripheral information.

## Decision

V4 filters every lane by authorized dataset, fuses rank positions using
reciprocal rank fusion and applies a bounded provenance-aware legal rerank.
PageRank remains telemetry only and has no authority to quarantine or change
artifact state.

## Consequences

Retrieval evaluation must report vector-only and lane ablations. Contradiction
and supersession are represented by provenance-bearing Assertion relations,
not inferred from centrality.

## Rollback

RRF parameters can be recalibrated behind the v4 contract, but score blending
or PageRank quarantine requires a new ADR and safety evidence.
