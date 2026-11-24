"""Unified entry point for the ``mesa`` local CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

from .configuration import MCPSettings
from .gateway.approval import OperationApprovalService
from .local_cli import handle_local_command, register_local_commands


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.group == "operations":
        try:
            asyncio.run(_execute_operation_decision(args))
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        return
    handle_local_command(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa", description="MESA local tools")
    commands = parser.add_subparsers(dest="group", required=True)
    register_local_commands(commands)

    operations = commands.add_parser(
        "operations", help="approve or reject durable gateway operations"
    )
    operation_commands = operations.add_subparsers(dest="command", required=True)
    for name in ("approve", "reject"):
        command = operation_commands.add_parser(
            name, help=f"{name} an operation awaiting operator review"
        )
        command.add_argument("operation_id")
        command.add_argument(
            "--control-db", type=Path, default=MCPSettings().gateway_control_db
        )
        command.add_argument(
            "--policy-db",
            type=Path,
            default=Path(os.environ.get("MESA_STORAGE_ROOT", "./storage"))
            .expanduser()
            .resolve()
            / "rbac_policy.db",
        )
        command.add_argument("--principal", default=os.environ.get("MESA_PRINCIPAL_ID"))
        command.add_argument("--reason")
    return parser


async def _execute_operation_decision(args: argparse.Namespace) -> None:
    from mesa_memory.security.rbac import AccessControl

    if not args.principal:
        raise ValueError(
            "operator principal is required; set MESA_PRINCIPAL_ID or use --principal"
        )
    engine = AsyncEngine(str(args.control_db.expanduser().resolve()))
    access = AccessControl(str(args.policy_db.expanduser().resolve()))
    await engine.initialize()
    await initialize_schema(engine)
    await access.initialize()
    try:
        result = await OperationApprovalService(
            engine=engine, access_control=access
        ).decide(
            operation_id=args.operation_id,
            decision="APPROVED" if args.command == "approve" else "REJECTED",
            decided_by=args.principal,
            reason=args.reason,
        )
    finally:
        await access.close()
        await engine.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
