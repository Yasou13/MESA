# Controlled atomic source edit method

- Planned targets: `mesa_memory/api/server.py`, `mesa_memory/security/rbac.py`, `mesa_api/router.py`, `scripts/run_server.py`, `tests/test_principal_authorization.py`.
- `scripts/run_server.py` was added after direct-caller discovery proved it mounted the same router with API-key authentication but without principal context (R2 of the same auth root cause).
- Every target was resolved under the repository root and rejected if symlinked.
- The recorded backup matched each pre-edit SHA-256 before a write was permitted.
- Each exact old block had to occur exactly once; no inferred or blind full-file rewrite was used.
- The transformed text was AST-parsed before write and again after temporary-file verification.
- The write used a same-directory temporary file, `flush`, `fsync`, mode preservation, and `os.replace`.
- Post-write `compileall`, `git diff --check`, focused target tests and related regressions were run.
- Rollback copies remain outside the repository at `/storage/mesa-lab/artifacts/WAVE-001/source-backup/`.
