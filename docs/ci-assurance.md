# CI assurance coverage

`MESA CI` and `MESA external release gates` use bounded, fail-closed release
checks.  This document records how the older assurance controls are preserved
without reintroducing a hard-coded API key or an unbounded full-suite run.

| Assurance area | Current control | Blocking |
|---|---|---|
| Secrets | TruffleHog scans the changed Git history; the local tracked-secret policy remains in `quality`. | Yes |
| Format, lint and typing | Black checks the runtime boundary; Ruff checks the repository; Mypy checks all canonical production packages with tracked progressive overrides. | Yes |
| Static-debt visibility | The weekly/manual `legacy-static-baseline` remains an artifact for formatting debt and type-debt trend review. | No, intentionally visible |
| Zero-cost mode | `zero-cost-contract` asserts configuration override and adapter selection with `MESA_ZERO_COST_MODE=true`. | Yes |
| Tenant isolation | `test_rbac_leak.py` is run with a four-minute process bound and JUnit/log evidence. | Yes |
| Compensating rollback | `test_chaos.py` is run with a four-minute process bound and JUnit/log evidence. | Yes |
| Graph isolation and poisoning | Real Kuzu isolation tests plus deterministic audit/threshold tests run with a four-minute process bound. | Yes |
| API canary | Compose verifies unauthenticated rejection plus authenticated health and readiness before restart checks. | Yes |

The production package Mypy command is a blocking quality gate. Its progressive
overrides remain explicitly listed in `pyproject.toml`; removing an override
requires the affected module to pass the strict defaults. The scheduled/manual
baseline continues to surface all-tree formatting and remaining type debt,
including paths deliberately outside the canonical production package set.

The old zero-cost command was `make test`, which executes the full suite.  It
is not used as a parity signal because that suite has a recorded hang
investigation.  The current gate is narrow and deterministic; expanding it
requires a bounded end-to-end zero-cost fixture that exercises the same
provider contract without external model downloads.

`scripts/canary_smoke_test.py` now requires `MESA_API_KEY` to be supplied by
its caller.  CI uses only the non-secret placeholder configured by the
workflow; no production-shaped fallback is retained.
