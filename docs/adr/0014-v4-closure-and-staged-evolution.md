# ADR 0014: V4 closure and staged evolution

- Status: Accepted for the rebuild boundary; the roadmap below is historical.
- Partial supersession: [ADR 0015](0015-bounded-adaptive-query-planning.md) advances
  bounded retrieval planning. The other future stages below are not implied
  to be implemented. Current behavior is in [architecture](../architecture-v4.md).
- Baseline: MESA 0.7.x at `c590188`
- Extends: ADR 0009, ADR 0012 and ADR 0013

## Context

The V4 canonical ledger and projection outbox exist, but a production-safe
projection rebuild also needs durable control state, one storage owner,
truthful capability reporting, deterministic verification and a reversible
cutover. LanceDB and Kùzu are currently shared by every dataset under a storage
root, so their physical layout cannot safely support tenant- or dataset-scoped
rebuilds.

Later architecture proposals depend on these closure guarantees. Implementing
them first would expand the number of storage and retrieval behaviours that
must be recovered without first establishing a reliable recovery boundary.

## Decision

The accepted rebuild boundary is independent of the historical feature order.
The following rules remain the operational constraints; current commands and
metadata behavior are maintained in the [rebuild runbook](../v4-rebuild-runbook.md).

The rebuild has the following hard boundaries:

- It is an offline runner, not an API or worker background task.
- `MESA_V4_REBUILD_ENABLED` defaults to `false`. Enabling submission does not
  make an online rebuild safe; operators must drain work and stop the combined
  runtime before running it.
- Its only scope is the complete storage root. Requests for tenant- or
  dataset-scoped rebuilds fail closed until vector and graph stores are
  physically partitioned.
- Canonical source/assertion content is not rebuilt. The runner rebuilds
  LanceDB and Kùzu projections from canonical ownership records and validates
  FTS integrity on a snapshot. Current operation/generation and representation
  metadata updates are detailed in the [rebuild runbook](../v4-rebuild-runbook.md).
- SQLite owns durable operation state and the single active projection
  generation pointer. Cutover changes that pointer atomically. The previous
  generation and the pre-run backup are retained until an explicit operator
  cleanup outside this release.
- Combined, worker-only and rebuild runtimes acquire the same storage-root
  writer lock before opening any writable store.
- Provider/version mismatch, path or symlink ambiguity, insufficient disk,
  parity failure and post-cutover health failure all fail closed. A failed
  post-cutover check restores the retained generation pointer.
- Dataset-bound MCP tools cannot submit a global rebuild. Administrative
  operation APIs require a control-plane `ADMIN` principal and expose only
  bounded, content-free status.

## Closure gates

Merge readiness requires deterministic V4 API contracts, migration and
repository contracts, rebuild fault injection, real-provider rehearsal where
available, formatting/lint/type checks and a content-free CI evidence
manifest. The manifest records schema and API identity, golden IDs, RRF
ablation, test results and small fixture-store counts without storing memory
content.

Production remains `NO-GO` after merge. A production `GO` additionally
requires a real-provider rehearsal on production-like disk and concurrency,
reviewed evidence and a 24-hour soak.

## Consequences

Operators get a resumable, auditable and reversible projection recovery path,
but the release deliberately does not offer online or scoped rebuilds. Storage
cost temporarily increases because backups and retained generations are not
automatically deleted. No online or scoped rebuild capability is implied.

## Historical roadmap

At baseline `c590188`, the proposed order was closure, focused DAO boundaries,
Ladybug, trusted assertion/bitemporal semantics, retrieval planning, MESA-Legal,
then community/global memory and hosted storage. This is a record of intent,
not an implementation checklist or a requirement to implement those stages.
[ADR 0015](0015-bounded-adaptive-query-planning.md) subsequently advanced bounded
query planning. Other stages require their own implementation and evidence;
current capabilities remain defined by the running API and architecture guide.

## Rollback

Disable new submissions, stop the offline runner, and keep mutation admission
closed while an operation is retryable. If a new generation was activated,
atomically restore the retained generation pointer before restarting the
combined runtime. Never delete the backup or previous generation as part of
automatic rollback.
