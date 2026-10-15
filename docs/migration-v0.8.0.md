# Preparing an upgrade to v0.8.0

v0.8.0 is a pending release target. Do not install a guessed tag or infer release
readiness from this page. Current metadata remains 0.7.1 until required gates
permit version preparation. This guide describes checked-out changes since
v0.7.1, not a completed production migration.

## Preconditions and changed contracts

Use a stopped, backed-up storage root and rehearse on a new disposable root.
Record source commit, lockfile, provider/model/revision/dimension/normalization,
Alembic head, artifacts and rollback path. RBAC has its own schema version.

Since v0.7.1, the tree includes immutable revision manifest activation, opaque
physical catalog identity, declared/manifest/chunk hash separation, durable
rebuild operations/generations, provider identity, assertion FTS/representation
and typed legal identity migrations. Current source expects Alembic head
`d5e6f7a8b9c0`. Do not assume an old database is compatible just because the
package version stayed 0.7.1 throughout the development branch.

New insertion/reprojection uses `assertion-semantic-v1`; migrated old vectors
are `legacy-v0` and cannot contribute as current semantic evidence. Reindex via
the supported full offline [rebuild](v4-rebuild-runbook.md). Unknown legacy provider
identity can be adopted only with external proof and a fresh unclaimed pending
operation. Matching dimensions alone do not prove matching embedding spaces.

Search/context carry temporal and jurisdiction filters; graph ON/OFF ablation
and bounded diagnostics are available. Context has atomic proof packing and an
opt-in adaptive mode. HTTP insert supports typed legal identity, while SDK
insert does not currently expose it. Invalid/frozen/idempotency-conflicting
source identity fails rather than silently overwriting. Semantic `REJECTED`
replay returns `409 NON_REPLAYABLE`.

## Upgrade and rollback sequence

1. Drain work, stop writers and take a checksummed backup under the writer-lock boundary.
2. Validate the backup and restore to a new empty staging root.
3. Apply forward migrations offline with the supported release environment.
4. Rebuild derived projections where required; check scope, provenance and parity.
5. Rehearse start, insert, poll, search, restart and negative authorization.
6. Cut over only with full release evidence and retain previous roots/generations.

Alembic downgrade is not the rollback strategy. Restore a verified prior backup
into a new empty root using the matching prior code/config, then validate before
switching traffic. V3 and V4 never share writable physical storage. Do not alter
official certification evidence as part of upgrade verification.
