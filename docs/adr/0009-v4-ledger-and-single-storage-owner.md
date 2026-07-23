# ADR 0009: V4 ledger and single storage owner

- Status: Accepted
- Supersedes: the v4 applicability of ADR 0004 and direct cross-store Saga
  descriptions; v3 compatibility behavior is unchanged.

## Context

SQLite, LanceDB and Kùzu cannot participate in one ACID transaction. Multiple
processes writing one embedded storage root also make fencing and recovery
ambiguous.

## Decision

V4 runs one `combined` storage owner. SQLite is the decision source for
mutation, pipeline, artifact ownership, projection and cleanup outboxes.
Ordered SQL→vector→graph projections use idempotent artifact IDs, fenced
leases, retries and reconciliation.

## Consequences

Horizontal API scale requires an admission architecture that does not add
storage writers. A failed projection is visible as retry/DLQ/BLOCKED rather
than being hidden by compensation. V3 retains its existing topology.

## Rollback

Disable the v4 deployment and return to the retained v3 lexical-core storage;
never point v3 and v4 writers at the same root.
