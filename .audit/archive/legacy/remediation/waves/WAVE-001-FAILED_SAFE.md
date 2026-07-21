# Failed Safe Report — WAVE-001

## Failure

The deterministic target test correctly reproduced SEC-002: an unmapped principal was allowed to create a session for another agent (HTTP 200 instead of 403).

## Patch attempt

The approved `apply_patch` source edit failed before writing due to the patch tool's `bwrap` namespace permission error.

## Safety state

No application source file was changed. The new failing test remains as regression evidence. No fallback full-file rewrite was attempted.

## Resume point

Restore an approved functional source patch mechanism, then start at PATCH, apply the minimal principal mapping/session-ownership change, and re-run the exact failing test.
