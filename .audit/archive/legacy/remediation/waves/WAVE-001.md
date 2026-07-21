# WAVE-001 — Tenant/Session Authorization

## Metadata

| Alan | Değer |
|---|---|
| Wave / Run | WAVE-001 / rem-20260719-030742 |
| Status / result | Blocked / BLOCKED |
| Last completed step | REPRODUCE preflight |
| Timestamp | 2026-07-19T03:12:00+03:00 |

## Scope and accepted contract

`SEC-002` and `LOGIC-001`: implement `DEC-REM-002` server-side principal-to-agent mapping, explicit permissions and server-owned sessions. Planned impact includes API auth dependency, router RBAC/session authorization, credential subject, SDK/MCP headers and targeted tests. No source file was changed.

## Reproduction plan

Create a deterministic two-principal fixture in `/storage/mesa-lab`: principal A must be rejected for agent B session/create/write/status/purge; an explicitly allowed principal must pass. Test must be model-disabled, mock-provider, offline and single-process.

## Failure evidence

The prerequisite test toolchain is absent: `pytest`, `fastapi`, `aiosqlite`, `httpx` and `pydantic` imports fail with `ModuleNotFoundError`; `pytest --version` exits 127. This is an environment prerequisite failure, not a finding resolution or false positive.

## Runtime and cross-system gate

Required evidence remains E2/E3 cross-principal authorization. Authentication, authorization, tenant/session isolation, SQLite ownership, SDK/MCP/API contract, worker context, config and migration are affected. No source/runtime change was made, so Docker, storage data and performance were not exercised.

## Rollback and reconciliation

No code changed; rollback is not required. `ENV-001` / `BOOT-001` are already canonical open blockers; no new finding was created. Canonical counts and `NO_GO` remain unchanged.

## Wave result

`BLOCKED` — Safe resume after a dependency-complete isolated lab environment exists, then restart at REPRODUCE.

## Safe-resume and patch result

The initial system-Python blocker was corrected: existing venv was repaired in place from official `.[dev]`; Python 3.13.11, pytest 9.1.1, required imports and `pip check` passed. With `PYTHON_DOTENV_DISABLED=1` and a synthetic key, `tests/test_principal_authorization.py` deterministically failed as expected: an unmapped `principal-a` requested `agent-b` and `/session/start` returned 200 instead of 403.

The minimal source patch (RBAC principal mapping/session ownership, server principal context, router `SESSION_CREATE` check) could not be applied because the required patch tool failed with `bwrap: No permissions to create a new namespace`. No application source file changed. Per remediation policy, no full-file source rewrite was attempted.

## Wave result update

`FAILED_SAFE` — retain the failing test and resume at PATCH only when the approved source patch mechanism can run.

## Tooling-only recovery and controlled atomic patch

- Resume timestamp: 2026-07-19T03:40:13+03:00
- Historical `FAILED_SAFE` record is retained. Recovery reason: `TOOLING_ERROR — SOURCE PATCH TRANSPORT FAILURE` (`bwrap` namespace denial), not product failure.
- User authorized a deterministic atomic fallback after the single failed patch transport attempt.
- Changed source: `mesa_memory/api/server.py`, `mesa_memory/security/rbac.py`, `mesa_api/router.py`; regression test: `tests/test_principal_authorization.py`.
- Minimal behavior fixed: an authenticated principal with no explicit server-side `SESSION_CREATE` mapping for the requested agent now receives HTTP 403. The router no longer turns the requested `agent_id` into authorization before that check.
- Validation: compile/diff checks passed; target test passed; focused RBAC/session/router regression set passed 30/30; server API-key→principal context check passed.
- Limitation: no E3 multi-principal HTTP/runtime or SDK/MCP contract proof exists; `tests/test_p0b_missing.py` could not collect because optional `openai` is absent. `SEC-002` is therefore `Fixed but not verified`; `LOGIC-001` remains open.

## Wave result update — recovery checkpoint

`FIXED_NOT_VERIFIED` — WAVE-001 completed its minimal verified E2 code/test objective, but does not close a canonical release blocker. Canonical P0/P1/release-blocker counts remain unchanged; fixed-but-not-verified count is now 2. The next dependency-eligible wave is WAVE-002.

## R2 direct composition-root recovery — 2026-07-19T03:42:50+03:00

`rg`/source inspection found that `scripts/run_server.py` mounts `create_memory_router` behind its own API-key middleware and therefore bypassed the main server’s new principal context. This is a direct caller effect of the same root cause, not a new independent wave. A fourth production source file was atomically patched: its normal authenticated path now attaches the same active configured principal and fails with 401 when no principal is configured. Direct middleware checks passed for valid and invalid synthetic keys. The explicit `--no-auth` development mode remains intentionally outside production authorization guarantees and is not a release proof.

## Clean restart attempt — clean-restart-01

| Alan | Değer |
|---|---|
| Run ID | rem-20260719-144002-W001-restart |
| Parent run | rem-20260719-030742 |
| Durum | RUNNING — PLAN |
| Gerekçe | Önceki denemeler environment ve source patch tooling hatalarıyla durdu; tarihsel evidence korunur fakat başarı kanıtı sayılmaz. |
| Başlangıç source doğrulaması | Dört WAVE-001 source dosyası ve hedef test hashleri önceki recorded after-hashlerle birebir eşleşti. |
| Başlangıç canonical durum | 9 P0, 40 P1, 43 teknik blocker, 1 fixed-but-not-verified, `NO_GO`. |

## Clean restart result — 2026-07-19

| Alan | Sonuç |
|---|---|
| Run | `rem-20260719-144002-W001-restart` |
| Result | `FIXED_NOT_VERIFIED` |
| Reproduction | Legacy 200 bu run’da gözlenmedi; current source hashleri pre-existing patched after-hashlerle eşleşti. |
| E2 evidence | 5 hedef authorization testi ve 33 ilgili RBAC/router/session testi geçti. |
| Source edit | Clean-restart application source edit yok; bounded test-only regression genişletmesi var. |
| E3/E4 | Yok; config isolation/runtime profile, SDK/MCP ve cross-endpoint proof eksik. |
| Canonical effect | `SEC-002` açık P0/release blocker; P0=9, P1=40, blocker=43, fixed-but-not-verified=2, `NO_GO`. |
| Safe resume | WAVE-002 planı; WAVE-001 verification gaps kapanış kanıtı sayılmaz. |

## WAVE-001-V follow-up registration

E3 authorization runtime verification is registered as `WAVE-001-V`, dependent on WAVE-005 config isolation/runtime profile. It does not reopen WAVE-001 and does not block independent WAVE-002 data-integrity work.
