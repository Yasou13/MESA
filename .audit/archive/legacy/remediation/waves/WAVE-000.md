# WAVE-000 — Canonical Identity ve Tenant Contract

## Metadata

| Alan | Değer |
|---|---|
| Wave | WAVE-000 |
| Status / result | Completed / VERIFIED_COMPLETE — DECISION RECORDED |
| Run ID | rem-20260719-030742 |
| Branch / HEAD | audit/production-readiness / c69d1f9c18844c393c26291db6c67628d82167f1 |
| Timestamp | 2026-07-19T03:07:42+03:00 |

## Scope

Yalnız kullanıcı tarafından kabul edilen identity, tenant, agent ownership ve session ownership contract'ını persist etmek. Kaynak, test, config, schema ve migration değiştirilmedi.

## Canonical findings and root cause

`SEC-002`, `SEC-003`, `RLS-001`, `SDK-003`, `LOGIC-001`. Global credential doğrulaması principal üretmezken session creation caller-supplied agent için WRITE grant veriyordu. Decision wave hiçbir finding'i kapatmaz.

## Accepted invariant

Principal yalnız explicit allowlist'teki agentlara ayrı permission set ile erişir; session ownership server-side saklanır; client identifiers authority değildir. `DEC-REM-002` USER/SERVICE/ADMIN/INTERNAL_WORKER, fail-closed least privilege, queue-scoped worker identity ve bounded legacy migration'ı tanımlar.

## Reproduction, tests and runtime gate

Decision persistence için test koşulmaz. WAVE-001 iki principal ile cross-agent session/create/write/status/purge negative tests ve E2/E3 evidence üretmelidir.

## Cross-system impact and rollback

Authentication, authorization, tenant/session isolation, SQLite ownership metadata, worker context, SDK, MCP, API contract, config/provisioning ve migration etkilenir. Kaynak değişikliği yapılmadığından Docker/runtime/performance etkilenmedi. Kaynak rollback gerekmez; contract yalnız yeni ADR ile supersede edilir.

## Audit reconciliation

`DEC-REM-002`, state, queue, wave summary ve evidence index güncellendi. Canonical sayılar 9 P0, 40 P1, 43 teknik blocker, 1 fixed-but-not-verified; Faz 14 `NO_GO` değişmedi.

## Wave result

`VERIFIED_COMPLETE — DECISION RECORDED`
