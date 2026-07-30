"""Offline/operator CLI for V4 credentials and authorization bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from .api_keys import APIKeyStore
from .rbac import AccessControl


def _default_policy_path() -> str:
    storage_root = os.environ.get("MESA_STORAGE_ROOT", "./storage")
    return str(Path(storage_root).expanduser().resolve() / "rbac_policy.db")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mesa-v4-admin",
        description=(
            "Provision principal-bound API keys and V4 catalog authorization. "
            "The command never accepts a plaintext key as an argument."
        ),
    )
    parser.add_argument(
        "--policy-db",
        default=_default_policy_path(),
        help="RBAC/API-key SQLite path (default: MESA_STORAGE_ROOT/rbac_policy.db)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue-key", help="Issue a key shown exactly once")
    issue.add_argument("--principal", required=True)
    issue.add_argument("--principal-type", default="SERVICE")
    issue.add_argument("--key-id")

    rotate = commands.add_parser("rotate-key", help="Revoke and replace an active key")
    rotate.add_argument("--key-id", required=True)

    revoke = commands.add_parser("revoke-key", help="Revoke an active key")
    revoke.add_argument("--key-id", required=True)

    role = commands.add_parser("grant-role", help="Grant OWNER/WRITER/READER")
    role.add_argument("--principal", required=True)
    role.add_argument("--tenant", required=True)
    role.add_argument("--workspace")
    role.add_argument("--dataset")
    role.add_argument("--role", choices=("OWNER", "WRITER", "READER"), required=True)

    agent = commands.add_parser(
        "grant-agent", help="Grant a principal one explicit agent permission"
    )
    agent.add_argument("--principal", required=True)
    agent.add_argument("--agent", required=True)
    agent.add_argument(
        "--permission",
        choices=(
            "READ",
            "WRITE",
            "SESSION_CREATE",
            "SESSION_READ",
            "SESSION_UPDATE",
            "STATUS_READ",
            "PURGE",
            "ADMIN",
        ),
        required=True,
    )

    dataset_permission = commands.add_parser(
        "grant-dataset-permission",
        help="Grant an explicit PURGE or ROLLBACK permission",
    )
    dataset_permission.add_argument("--principal", required=True)
    dataset_permission.add_argument("--tenant", required=True)
    dataset_permission.add_argument("--dataset", required=True)
    dataset_permission.add_argument(
        "--permission", choices=("PURGE", "ROLLBACK"), required=True
    )
    return parser


async def _run(args: argparse.Namespace) -> str:
    policy_path = Path(args.policy_db).expanduser().resolve()
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    key_store = APIKeyStore(str(policy_path))
    access = AccessControl(str(policy_path))
    await key_store.initialize()
    await access.initialize()
    try:
        if args.command == "issue-key":
            credential = await key_store.issue_key(
                principal_id=args.principal,
                principal_type=args.principal_type,
                key_id=args.key_id,
            )
            return credential
        if args.command == "rotate-key":
            return await key_store.rotate_key(args.key_id)
        if args.command == "revoke-key":
            if not await key_store.revoke_key(args.key_id):
                raise ValueError("active key not found")
            return f"revoked:{args.key_id}"
        if args.command == "grant-role":
            if args.dataset and not args.workspace:
                raise ValueError("--dataset requires --workspace")
            await access.grant_scope_role(
                args.principal,
                tenant_id=args.tenant,
                workspace_id=args.workspace,
                dataset_id=args.dataset,
                role=args.role,
            )
            scope = args.dataset or args.workspace or args.tenant
            return f"role-granted:{args.principal}:{scope}:{args.role}"
        if args.command == "grant-agent":
            await access.grant_principal_permission(
                args.principal, args.agent, args.permission
            )
            return (
                f"agent-permission-granted:{args.principal}:"
                f"{args.agent}:{args.permission}"
            )
        if args.command == "grant-dataset-permission":
            await access.grant_dataset_permission(
                args.principal,
                tenant_id=args.tenant,
                dataset_id=args.dataset,
                permission=args.permission,
            )
            return (
                f"dataset-permission-granted:{args.principal}:"
                f"{args.dataset}:{args.permission}"
            )
        raise ValueError("unsupported command")
    finally:
        await access.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output = asyncio.run(_run(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"mesa-v4-admin: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
