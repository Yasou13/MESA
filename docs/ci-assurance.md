# CI assurance coverage

`MESA CI` and `MESA external release gates` use bounded, fail-closed release
checks.  This document records how the older assurance controls are preserved
without reintroducing a hard-coded API key or an unbounded full-suite run.

| Assurance area | Current control | Blocking |
|---|---|---|
| Secrets | TruffleHog scans the changed Git history; the local tracked-secret policy remains in `quality`. | Yes |
| Format and typing | Black and Mypy enforce the production runtime entrypoint boundary. | Yes |
| Full-tree static debt | The weekly/manual `legacy-static-baseline` records Black and Mypy failures as artifacts. | No, intentionally visible |
| Zero-cost mode | `zero-cost-contract` asserts configuration override and adapter selection with `MESA_ZERO_COST_MODE=true`. | Yes |
| Tenant isolation | `test_rbac_leak.py` is run with a four-minute process bound and JUnit/log evidence. | Yes |
| Compensating rollback | `test_chaos.py` is run with a four-minute process bound and JUnit/log evidence. | Yes |
| Graph isolation and poisoning | Real Kuzu isolation tests plus deterministic audit/threshold tests run with a four-minute process bound. | Yes |
| API canary | Compose verifies unauthenticated rejection plus authenticated health and readiness before restart checks. | Yes |

The old all-tree Black and Mypy commands are not presented as passing release
gates: the current verified baseline is 42 files Black would reformat and 23
Mypy errors.  They remain scheduled/manual evidence until both commands exit
zero.  At that point, move them into the blocking `quality` job and remove
`legacy-static-baseline`; do not remove the results merely to make CI green.

The old zero-cost command was `make test`, which executes the full suite.  It
is not used as a parity signal because that suite has a recorded hang
investigation.  The current gate is narrow and deterministic; expanding it
requires a bounded end-to-end zero-cost fixture that exercises the same
provider contract without external model downloads.

`scripts/canary_smoke_test.py` now requires `MESA_API_KEY` to be supplied by
its caller.  CI uses only the non-secret placeholder configured by the
workflow; no production-shaped fallback is retained.
