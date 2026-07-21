No regression occurred. Controlled recovery did not start because the primary deterministic test prerequisite is absent.

Patch attempt: minimal `apply_patch` for `mesa_memory/security/rbac.py`, `mesa_memory/api/server.py`, `mesa_api/router.py` and the target test adjustment.
Result: patch tool filesystem sandbox failed before source modification (`bwrap` namespace permission error).
Safety action: no alternative full-file source rewrite; wave stopped FAILED_SAFE.
