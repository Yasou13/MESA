# CLI reference

The entry points are declared in `pyproject.toml`. Run `.venv/bin/<command>
--help` after the locked install. These commands do not have a universal
`--version`; read package metadata with:

```bash
.venv/bin/python -c "from importlib.metadata import version; print(version('mesa-memory'))"
```

| Command | Scope |
|---|---|
| `mesa-v4-admin` | Offline credentials and ACL provisioning; defaults to `MESA_STORAGE_ROOT/rbac_policy.db` |
| `mesa-recovery` | Trusted-root checksummed backup, validate and new-root restore |
| `mesa-v4-rebuild` | Offline full-root projection rebuild/adoption; never an online worker |
| `mesa-mcp` | stdio server; V3 default, explicit V4 settings supported |
| `mesa-mcp-gateway` | Independently supervised HTTP/Streamable HTTP control gateway |
| `mesa-antigravity-bridge` | Binding-scoped stdio bridge with encrypted write spool |
| `mesa` | Codex/Antigravity install, doctor, profiles, credential lifecycle and operations approvals |

## Admin bootstrap

```bash
.venv/bin/mesa-v4-admin --help
.venv/bin/mesa-v4-admin grant-role --principal docs-principal --tenant docs-tenant --role OWNER
.venv/bin/mesa-v4-admin grant-agent --principal docs-principal --agent docs-agent --permission SESSION_CREATE
```

Use these only against the intended stopped/new policy database. `issue-key`
prints the credential once; `rotate-key --key-id` revokes/replaces it.
`grant-control` assigns `ADMIN`. `grant-dataset-permission` explicitly assigns
`PURGE`/`ROLLBACK`; role inheritance alone does not give those operations.
Dataset role grants require both `--workspace` and `--dataset`. Corresponding
`revoke-role`/`revoke-dataset-permission` remove authorization.

## Recovery, rebuild and MCP

Use [operations](RUNBOOK.md) and [rebuild](v4-rebuild-runbook.md) for safety,
trusted paths, locking, status, retry/lease, exit codes and retained generations.
Never use arbitrary raw-store cleanup as a CLI substitute.
[MCP](../README_MCP.md) describes binding credentials, encryption and local
operator approval. `mesa operations approve/reject` acts on durable operation
IDs with server-side ADMIN authority; it does not bypass gateway policy.

`python -m mesa_memory.runtime_entrypoint` selects the explicit runtime profile;
there is no `mesa serve` command. `mesa-benchmark` belongs to the separate
benchmark project and is not installed by this Core package.
