# WAVE-003 — WAL, alignment, claim ve replay

## Metadata

| Alan | Değer |
|---|---|
| Wave | WAVE-003 |
| Status | FIXED_NOT_VERIFIED |
| Run ID | rem-20260719-161500-W003 |
| Branch | audit/production-readiness |
| Start HEAD | c69d1f9c18844c393c26291db6c67628d82167f1 |
| Started/completed | 2026-07-19T16:15:00+03:00 / 2026-07-19T16:35:00+03:00 |

## Scope

`DATA-005` ve `CONC-002`: WAL/alignment mutation barrier ile raw-log claim/lease/terminal transition/replay correctness.

## Canonical findings

Her iki finding kapanmadı. E2 ile `Fixed but not verified` durumuna alındı; P0/P1/blocker sayıları ve `NO_GO` değişmedi.

## Source analysis and root cause

Plain boolean migration state, bulk WAL delete, per-row owner/ack eksikliği, complete mutation barrier eksikliği ve raw-log read→update split’i kanıtlandı. Ayrıntı: `evidence/WAVE-003/source-analysis.md` ve `root-cause.md`.

## Changes

- Additive Alembic migration: `e9b7c3a1d4f2_add_claim_leases.py`.
- DAO: CAS claim/lease/fencing, guarded terminal transition, expired-lease recovery, WAL claim/release/ack/replay; vector I/O SQLite write transaction dışında.
- Worker: gerçek `MemoryDAO` çağrısı için atomic claim ve fenced finalization.
- VectorEngine: `_mutation_lock` snapshot-to-promotion alignment yaşam döngüsünü kapsar.
- Test: `tests/test_wal_claim_replay_contract.py`.

## Tests

Pre-fix 2 deterministic failure; post-fix 2 target test passed. WAVE-002 regression 10 passed. General DAO suite has 13 existing WAVE-002 graph fail-closed mock-fixture failure; worker caller suite user-owned trace-file yazım riski nedeniyle çalıştırılmadı. E3 yok.

## Remaining risk

Real Lance/Kuzu/process crash/restart, alignment dual-owner lease/fencing across processes, caller-level side-effect exact-once, startup dispatcher and API/worker runtime proof yoktur. WAVE-003-V queued.

## Wave result

`FIXED_NOT_VERIFIED`. Canonical counts remain P0=9, P1=40, release blocker=43; fixed-but-not-verified=7; final decision remains `NO_GO`.
