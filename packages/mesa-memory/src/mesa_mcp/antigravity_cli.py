"""Secret-safe local lifecycle commands for the Antigravity bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import stat
from pathlib import Path
from typing import Any, Callable

import httpx

from .codex_cli import _backup, _open_control
from .workspace import workspace_fingerprint


def add_parser(commands: argparse._SubParsersAction, common: Callable) -> None:
    antigravity = commands.add_parser(
        "antigravity", help="manage the Antigravity MESA bridge"
    )
    antigravity.set_defaults(group="antigravity")
    sub = antigravity.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    common(install)
    install.add_argument("--client-id")
    install.add_argument("--display-name")
    install.add_argument(
        "--principal-id", default=os.environ.get("MESA_PRINCIPAL_ID", "antigravity")
    )
    install.add_argument("--tenant-id", default=os.environ.get("MESA_TENANT_ID", "default"))
    install.add_argument("--workspace-id", default=os.environ.get("MESA_WORKSPACE_ID", "default"))
    install.add_argument("--dataset-id", default=os.environ.get("MESA_DATASET_ID", "default"))
    for name in ("doctor", "status", "disable", "uninstall", "bridge"):
        command = sub.add_parser(name)
        common(command)
    rotate = sub.add_parser("rotate-token")
    common(rotate)
    rotate.add_argument("--client-id")
    profile = sub.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    get_profile = profile_commands.add_parser("get")
    common(get_profile)
    set_profile = profile_commands.add_parser("set")
    common(set_profile)
    set_profile.add_argument("--max-records", type=int)
    set_profile.add_argument("--max-tokens", type=int)
    set_profile.add_argument("--memory-types", nargs="+")


def main_for_args(args: argparse.Namespace) -> None:
    try:
        if args.command == "bridge":
            _run_bridge(args)
        else:
            asyncio.run(_execute(args))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"mesa antigravity: {exc}")


async def _execute(args: argparse.Namespace) -> None:
    root = args.workspace.resolve()
    if args.command == "install":
        await _install(args, root)
    elif args.command == "doctor":
        _doctor(root)
    elif args.command == "status":
        await _status(args, root)
    elif args.command == "rotate-token":
        await _rotate(args, root)
    elif args.command == "profile":
        await _profile(args, root)
    elif args.command == "disable":
        await _disable(args, root)
    elif args.command == "uninstall":
        await _disable(args, root)
        _uninstall_config(root)
        print(json.dumps({"status": "uninstalled"}))


async def _install(args: argparse.Namespace, root: Path) -> None:
    if not root.is_dir():
        raise ValueError("workspace must be an existing directory")
    fingerprint = workspace_fingerprint(root)
    client_id = args.client_id or f"antigravity-{fingerprint[:20]}"
    env_path = _env_path(fingerprint)
    existing = _read_env(env_path)
    control = await _open_control(args)
    try:
        client = await control.client_repo.get_client(client_id)
        if client is None:
            await control.client_repo.create_client(
                client_id,
                args.display_name or f"Antigravity · {root.name}",
                "antigravity",
                args.principal_id,
            )
        elif client["client_type"] != "antigravity":
            raise ValueError("client_id already belongs to another client type")
        binding_id = await control.client_repo.add_project_binding(
            client_id, fingerprint, args.tenant_id, args.workspace_id, args.dataset_id
        )
        await control.binding_profile_repo.ensure(binding_id)
        credential_id = existing.get("MESA_ANTIGRAVITY_CREDENTIAL_ID")
        summary = await control.credential_repo.get_summary(credential_id) if credential_id else None
        if not (
            summary
            and summary["status"] == "ACTIVE"
            and summary["binding_id"] == binding_id
            and existing.get("MESA_ANTIGRAVITY_MCP_TOKEN")
        ):
            record, token = await control.credential_repo.issue(
                client_id, binding_id, token_kind="antigravity"
            )
            _write_env(env_path, token, record["credential_id"], args.gateway_url)
            issued = True
        else:
            issued = False
    finally:
        await control.close()
    _install_config(root)
    print(json.dumps({"status": "installed", "client_id": client_id, "binding_id": binding_id, "credential_issued": issued}))


async def _status(args: argparse.Namespace, root: Path) -> None:
    env = _read_env(_env_path(workspace_fingerprint(root)))
    result: dict[str, Any] = {"configured": bool(env)}
    credential_id = env.get("MESA_ANTIGRAVITY_CREDENTIAL_ID")
    if credential_id:
        control = await _open_control(args)
        try:
            result["credential"] = await control.credential_repo.get_summary(credential_id)
        finally:
            await control.close()
    print(json.dumps(result))


async def _rotate(args: argparse.Namespace, root: Path) -> None:
    fingerprint = workspace_fingerprint(root)
    path = _env_path(fingerprint)
    old = _read_env(path)
    old_id = old.get("MESA_ANTIGRAVITY_CREDENTIAL_ID")
    if not old_id:
        raise ValueError("no installed Antigravity credential")
    control = await _open_control(args)
    new_id: str | None = None
    try:
        previous = await control.credential_repo.get_summary(old_id)
        if previous is None or previous["status"] != "ACTIVE":
            raise ValueError("installed credential is not active")
        record, token = await control.credential_repo.issue(
            args.client_id or previous["client_id"],
            previous["binding_id"],
            token_kind="antigravity",
        )
        new_id = record["credential_id"]
        await _verify_handshake(args.gateway_url, token, fingerprint)
        _write_env(path, token, new_id, args.gateway_url)
        if not await control.credential_repo.revoke(old_id):
            raise RuntimeError("previous credential could not be revoked")
    except Exception:
        if new_id:
            await control.credential_repo.revoke(new_id)
        _restore_env(path, old)
        raise
    finally:
        await control.close()
    print(json.dumps({"status": "rotated", "credential_id": new_id}))


async def _disable(args: argparse.Namespace, root: Path) -> None:
    path = _env_path(workspace_fingerprint(root))
    env = _read_env(path)
    credential_id = env.get("MESA_ANTIGRAVITY_CREDENTIAL_ID")
    if credential_id:
        control = await _open_control(args)
        try:
            await control.credential_repo.revoke(credential_id)
        finally:
            await control.close()
    if path.exists():
        path.unlink()
    print(json.dumps({"status": "disabled"}))


async def _profile(args: argparse.Namespace, root: Path) -> None:
    env = _read_env(_env_path(workspace_fingerprint(root)))
    credential_id = env.get("MESA_ANTIGRAVITY_CREDENTIAL_ID")
    if not credential_id:
        raise ValueError("no installed Antigravity credential")
    control = await _open_control(args)
    try:
        credential = await control.credential_repo.get_summary(credential_id)
        if credential is None:
            raise ValueError("installed credential is not known to the control plane")
        if args.profile_command == "get":
            result = await control.binding_profile_repo.get(credential["binding_id"])
        else:
            result = await control.binding_profile_repo.update(
                credential["binding_id"],
                max_records=args.max_records,
                max_tokens=args.max_tokens,
                memory_types=args.memory_types,
            )
    finally:
        await control.close()
    print(json.dumps(result, ensure_ascii=False))


def _doctor(root: Path) -> None:
    path = _env_path(workspace_fingerprint(root))
    issues = []
    if not path.exists():
        issues.append("credential env file is missing")
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        issues.append("credential env file permissions must be 0600")
    if not _has_bridge_config(root):
        issues.append("MESA Antigravity bridge config is missing")
    print(json.dumps({"status": "ok" if not issues else "degraded", "issues": issues}))
    if issues:
        raise RuntimeError("doctor found configuration issues")


def _run_bridge(args: argparse.Namespace) -> None:
    root = args.workspace.resolve()
    env = _read_env(_env_path(workspace_fingerprint(root)))
    if not env.get("MESA_ANTIGRAVITY_MCP_TOKEN"):
        raise ValueError("no installed Antigravity credential")
    os.environ.update(env)
    os.environ["MESA_WORKSPACE_ROOT"] = str(root)
    from .antigravity_bridge import main

    main()


async def _verify_handshake(gateway_url: str, token: str, fingerprint: str) -> None:
    async with httpx.AsyncClient(timeout=8.0, headers={"Authorization": f"Bearer {token}"}) as client:
        response = await client.post(
            gateway_url.rstrip("/") + "/mcp/v1/handshake",
            json={"workspace_fingerprint": fingerprint, "client_instance_id": "rotation-check", "mcp_protocol_version": "2025-03-26"},
        )
        response.raise_for_status()


def _env_path(fingerprint: str) -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "mesa" / "antigravity" / f"{fingerprint}.env"


def _write_env(path: Path, token: str, credential_id: str, gateway_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        f"MESA_ANTIGRAVITY_MCP_TOKEN={shlex.quote(token)}\n"
        f"MESA_ANTIGRAVITY_CREDENTIAL_ID={shlex.quote(credential_id)}\n"
        f"MESA_GATEWAY_URL={shlex.quote(gateway_url.rstrip('/'))}\n",
        encoding="utf-8",
    )
    temp.chmod(0o600)
    temp.replace(path)
    path.chmod(0o600)


def _restore_env(path: Path, values: dict[str, str]) -> None:
    if values.get("MESA_ANTIGRAVITY_MCP_TOKEN") and values.get("MESA_ANTIGRAVITY_CREDENTIAL_ID"):
        _write_env(path, values["MESA_ANTIGRAVITY_MCP_TOKEN"], values["MESA_ANTIGRAVITY_CREDENTIAL_ID"], values.get("MESA_GATEWAY_URL", "http://127.0.0.1:8765"))


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.startswith("MESA_"):
            parsed = shlex.split(value)
            values[key] = parsed[0] if parsed else ""
    return values


def _config_path(root: Path) -> Path:
    return root / ".agents" / "mcp_config.json"


def _install_config(root: Path) -> None:
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    document: dict[str, Any] = {"mcpServers": {}}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            document = loaded
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp_config.json mcpServers must be an object")
    servers["mesa-memory"] = {
        "command": "mesa",
        "args": ["antigravity", "bridge", "--workspace", str(root)],
        "cwd": str(root),
        "env": {"MESA_LOG_LEVEL": "INFO"},
        "disabled": False,
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _uninstall_config(root: Path) -> None:
    path = _config_path(root)
    if not path.exists():
        return
    _backup(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, dict) and isinstance(document.get("mcpServers"), dict):
        document["mcpServers"].pop("mesa-memory", None)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _has_bridge_config(root: Path) -> bool:
    path = _config_path(root)
    if not path.exists():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    server = document.get("mcpServers", {}).get("mesa-memory", {})
    return (
        isinstance(server, dict)
        and server.get("command") == "mesa"
        and server.get("args", [])[:2] == ["antigravity", "bridge"]
    )
