"""Local, secret-safe lifecycle commands for the Codex MCP integration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

from .configuration import MCPSettings
from .gateway.middleware import ControlPlaneMiddleware
from .workspace import workspace_fingerprint

_TOOLS = [
    "mesa_health",
    "mesa_recall",
    "mesa_remember",
    "mesa_improve",
    "mesa_forget",
    "mesa_get_operation_status",
]
_MANAGED_START = "# >>> MESA CODEX (managed)"
_MANAGED_END = "# <<< MESA CODEX (managed)"


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.group == "antigravity":
        from .antigravity_cli import main_for_args

        main_for_args(args)
        return
    if args.command == "hook":
        from . import codex_hooks

        raise SystemExit(codex_hooks.main(args.event))
    try:
        if args.command == "run":
            _run(args)
        else:
            asyncio.run(_execute(args))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa", description="MESA local tools")
    commands = parser.add_subparsers(dest="group", required=True)
    codex = commands.add_parser("codex", help="manage Codex project memory")
    codex.set_defaults(group="codex")
    sub = codex.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, default=Path.cwd())
        command.add_argument(
            "--control-db", type=Path, default=MCPSettings().gateway_control_db
        )
        command.add_argument(
            "--gateway-url",
            default=os.environ.get("MESA_CODEX_GATEWAY_URL", "http://127.0.0.1:8765"),
        )

    install = sub.add_parser("install", help="install MESA into this trusted project")
    common(install)
    install.add_argument("--client-id")
    install.add_argument("--display-name")
    install.add_argument(
        "--principal-id", default=os.environ.get("MESA_PRINCIPAL_ID", "local-codex")
    )
    install.add_argument(
        "--tenant-id", default=os.environ.get("MESA_TENANT_ID", "default")
    )
    install.add_argument(
        "--workspace-id", default=os.environ.get("MESA_WORKSPACE_ID", "default")
    )
    install.add_argument(
        "--dataset-id", default=os.environ.get("MESA_DATASET_ID", "default")
    )

    for name in ("doctor", "status", "disable", "uninstall"):
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
    set_profile.add_argument("--session-start", choices=("on", "off"))
    set_profile.add_argument("--post-compact", choices=("on", "off"))
    set_profile.add_argument("--max-records", type=int)
    set_profile.add_argument("--max-tokens", type=int)
    set_profile.add_argument("--memory-types", nargs="+")

    run = sub.add_parser("run", help="launch Codex with the protected MESA token")
    common(run)
    run.add_argument("codex_args", nargs=argparse.REMAINDER)
    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("event", choices=("start", "end", "post-compact"))
    from .antigravity_cli import add_parser as add_antigravity_parser

    add_antigravity_parser(commands, common)
    return parser


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
    elif args.command == "disable":
        await _disable(args, root)
    elif args.command == "uninstall":
        await _uninstall(args, root)
    elif args.command == "profile":
        await _profile(args, root)


async def _open_control(args: argparse.Namespace) -> ControlPlaneMiddleware:
    engine = AsyncEngine(str(args.control_db))
    await engine.initialize()
    await initialize_schema(engine)
    control = ControlPlaneMiddleware(engine=engine)
    await control.initialize()
    return control


async def _install(args: argparse.Namespace, root: Path) -> None:
    if not root.is_dir():
        raise ValueError("workspace must be an existing directory")
    fingerprint = workspace_fingerprint(root)
    client_id = args.client_id or f"codex-{fingerprint[:20]}"
    display_name = args.display_name or f"Codex · {root.name}"
    env_path = _env_path(fingerprint)
    existing = _read_env(env_path)
    control = await _open_control(args)
    try:
        client = await control.client_repo.get_client(client_id)
        if client is None:
            await control.client_repo.create_client(
                client_id, display_name, "codex", args.principal_id
            )
        elif client["client_type"] != "codex":
            raise ValueError("client_id already belongs to a non-Codex client")
        else:
            await control.client_repo.update_client(
                client_id, display_name=display_name, principal_id=args.principal_id
            )
        binding_id = await control.client_repo.add_project_binding(
            client_id,
            fingerprint,
            args.tenant_id,
            args.workspace_id,
            args.dataset_id,
        )
        await control.codex_profile_repo.ensure(binding_id)
        credential_id = existing.get("MESA_CODEX_CREDENTIAL_ID")
        summary = (
            await control.credential_repo.get_summary(credential_id)
            if credential_id
            else None
        )
        if not (
            summary
            and summary["status"] == "ACTIVE"
            and summary["binding_id"] == binding_id
            and existing.get("MESA_CODEX_MCP_TOKEN")
        ):
            record, token = await control.credential_repo.issue(client_id, binding_id)
            _write_env(env_path, token, record["credential_id"], args.gateway_url)
            issued = True
        else:
            issued = False
    finally:
        await control.close()
    _install_project_files(root, args.gateway_url)
    print(
        json.dumps(
            {
                "status": "installed",
                "client_id": client_id,
                "binding_id": binding_id,
                "credential_issued": issued,
            }
        )
    )


async def _status(args: argparse.Namespace, root: Path) -> None:
    fingerprint = workspace_fingerprint(root)
    env = _read_env(_env_path(fingerprint))
    result: dict[str, Any] = {
        "workspace_fingerprint": fingerprint,
        "configured": bool(env),
    }
    credential_id = env.get("MESA_CODEX_CREDENTIAL_ID")
    if credential_id:
        control = await _open_control(args)
        try:
            summary = await control.credential_repo.get_summary(credential_id)
            result["credential"] = summary
        finally:
            await control.close()
    print(json.dumps(result, ensure_ascii=False))


async def _rotate(args: argparse.Namespace, root: Path) -> None:
    fingerprint = workspace_fingerprint(root)
    env_path = _env_path(fingerprint)
    old = _read_env(env_path)
    old_id = old.get("MESA_CODEX_CREDENTIAL_ID")
    if not old_id or not old.get("MESA_CODEX_MCP_TOKEN"):
        raise ValueError(
            "no installed Codex credential; run `mesa codex install` first"
        )
    control = await _open_control(args)
    new_id: str | None = None
    try:
        old_summary = await control.credential_repo.get_summary(old_id)
        if old_summary is None or old_summary["status"] != "ACTIVE":
            raise ValueError("installed credential is not active")
        client_id = args.client_id or old_summary["client_id"]
        record, token = await control.credential_repo.issue(
            client_id, old_summary["binding_id"]
        )
        new_id = record["credential_id"]
        await _verify_gateway_credential(args.gateway_url, token)
        _write_env(env_path, token, new_id, args.gateway_url)
        if not await control.credential_repo.revoke(old_id):
            raise RuntimeError(
                "new token was saved but previous credential was not revoked"
            )
    except Exception:
        if new_id:
            await control.credential_repo.revoke(new_id)
        _restore_env(env_path, old)
        raise
    finally:
        await control.close()
    print(json.dumps({"status": "rotated", "credential_id": new_id}))


async def _disable(args: argparse.Namespace, root: Path) -> None:
    env_path = _env_path(workspace_fingerprint(root))
    env = _read_env(env_path)
    credential_id = env.get("MESA_CODEX_CREDENTIAL_ID")
    if credential_id:
        control = await _open_control(args)
        try:
            await control.credential_repo.revoke(credential_id)
        finally:
            await control.close()
    if env_path.exists():
        env_path.unlink()
    print(json.dumps({"status": "disabled"}))


async def _uninstall(args: argparse.Namespace, root: Path) -> None:
    await _disable(args, root)
    _uninstall_project_files(root)
    print(json.dumps({"status": "uninstalled"}))


async def _profile(args: argparse.Namespace, root: Path) -> None:
    fingerprint = workspace_fingerprint(root)
    env = _read_env(_env_path(fingerprint))
    credential_id = env.get("MESA_CODEX_CREDENTIAL_ID")
    if not credential_id:
        raise ValueError("no installed Codex credential")
    control = await _open_control(args)
    try:
        credential = await control.credential_repo.get_summary(credential_id)
        if credential is None:
            raise ValueError("installed credential is not known to the control plane")
        if args.profile_command == "get":
            result = await control.codex_profile_repo.get(credential["binding_id"])
        else:
            result = await control.codex_profile_repo.update(
                credential["binding_id"],
                session_start_enabled=_optional_bool(args.session_start),
                post_compact_enabled=_optional_bool(args.post_compact),
                max_records=args.max_records,
                max_tokens=args.max_tokens,
                memory_types=args.memory_types,
            )
    finally:
        await control.close()
    print(json.dumps(result, ensure_ascii=False))


def _doctor(root: Path) -> None:
    fingerprint = workspace_fingerprint(root)
    env_path = _env_path(fingerprint)
    issues: list[str] = []
    if not env_path.exists():
        issues.append("credential env file is missing")
    elif stat.S_IMODE(env_path.stat().st_mode) & 0o077:
        issues.append("credential env file permissions must be 0600")
    config_exists = _config_path(root).exists()
    if not config_exists or "[mcp_servers.mesa]" not in _config_path(root).read_text(
        encoding="utf-8"
    ):
        issues.append("MESA MCP config is missing")
    if not _hooks_path(root).exists():
        issues.append("MESA hooks config is missing")
    result = {
        "status": "ok" if not issues else "degraded",
        "issues": issues,
        "run": f"mesa codex run --workspace {shlex.quote(str(root))}",
    }
    print(json.dumps(result, ensure_ascii=False))
    if issues:
        raise RuntimeError("doctor found configuration issues")


def _run(args: argparse.Namespace) -> None:
    env = _read_env(_env_path(workspace_fingerprint(args.workspace.resolve())))
    token = env.get("MESA_CODEX_MCP_TOKEN")
    if not token:
        raise ValueError(
            "no installed Codex credential; run `mesa codex install` first"
        )
    command = args.codex_args or ["codex"]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        command = ["codex"]
    if command[0] != "codex":
        command.insert(0, "codex")
    child_env = os.environ.copy()
    child_env.update(env)
    raise SystemExit(subprocess.run(command, env=child_env, check=False).returncode)


def _config_path(root: Path) -> Path:
    return root / ".codex" / "config.toml"


def _hooks_path(root: Path) -> Path:
    return root / ".codex" / "hooks.json"


def _env_path(fingerprint: str) -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "mesa" / "codex" / f"{fingerprint}.env"


def _write_env(path: Path, token: str, credential_id: str, gateway_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    content = (
        f"MESA_CODEX_MCP_TOKEN={shlex.quote(token)}\n"
        f"MESA_CODEX_CREDENTIAL_ID={shlex.quote(credential_id)}\n"
        f"MESA_CODEX_GATEWAY_URL={shlex.quote(gateway_url.rstrip('/'))}\n"
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _restore_env(path: Path, values: dict[str, str]) -> None:
    token = values.get("MESA_CODEX_MCP_TOKEN")
    credential_id = values.get("MESA_CODEX_CREDENTIAL_ID")
    if token and credential_id:
        _write_env(
            path,
            token,
            credential_id,
            values.get("MESA_CODEX_GATEWAY_URL", "http://127.0.0.1:8765"),
        )


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.startswith("MESA_CODEX_"):
            parsed = shlex.split(value)
            result[key] = parsed[0] if parsed else ""
    return result


def _backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(path.name + f".mesa-backup-{stamp}"))


def _install_project_files(root: Path, gateway_url: str) -> None:
    config = _config_path(root)
    config.parent.mkdir(parents=True, exist_ok=True)
    _backup(config)
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    block = _managed_config(gateway_url)
    if _MANAGED_START in existing and _MANAGED_END in existing:
        existing = re.sub(
            re.escape(_MANAGED_START) + r".*?" + re.escape(_MANAGED_END),
            block.rstrip(),
            existing,
            flags=re.S,
        )
    else:
        pattern = (
            r"(?ms)^\[mcp_servers\.mesa\].*?(?=^\[(?!mcp_servers\.mesa(?:\.|\]))|\Z)"
        )
        existing, count = re.subn(pattern, "", existing)
        separator = "\n" if existing.strip() else ""
        existing = existing.rstrip() + separator + block
    config.write_text(existing.rstrip() + "\n", encoding="utf-8")
    hooks = _hooks_path(root)
    _backup(hooks)
    document: dict[str, Any] = {
        "description": "MESA project-memory integration for Codex.",
        "hooks": {},
    }
    if hooks.exists():
        parsed = json.loads(hooks.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            document = parsed
    hook_map = document.setdefault("hooks", {})
    if not isinstance(hook_map, dict):
        raise ValueError("hooks.json hooks must be an object")
    for event, entry in _managed_hooks().items():
        items = hook_map.get(event, [])
        if not isinstance(items, list):
            raise ValueError(f"hooks.json {event} must be an array")
        hook_map[event] = [item for item in items if not _is_mesa_hook(item)] + [entry]
    hooks.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _uninstall_project_files(root: Path) -> None:
    config = _config_path(root)
    if config.exists():
        _backup(config)
        content = config.read_text(encoding="utf-8")
        content = re.sub(
            re.escape(_MANAGED_START) + r".*?" + re.escape(_MANAGED_END) + r"\n?",
            "",
            content,
            flags=re.S,
        )
        config.write_text(
            content.strip() + ("\n" if content.strip() else ""), encoding="utf-8"
        )
    hooks = _hooks_path(root)
    if hooks.exists():
        _backup(hooks)
        document = json.loads(hooks.read_text(encoding="utf-8"))
        hook_map = document.get("hooks", {}) if isinstance(document, dict) else {}
        if isinstance(hook_map, dict):
            for event, items in list(hook_map.items()):
                if isinstance(items, list):
                    retained = [item for item in items if not _is_mesa_hook(item)]
                    if retained:
                        hook_map[event] = retained
                    else:
                        hook_map.pop(event)
        hooks.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _managed_config(gateway_url: str) -> str:
    tools = ", ".join(json.dumps(tool) for tool in _TOOLS)
    return f"""{_MANAGED_START}
[mcp_servers.mesa]
url = {json.dumps(gateway_url.rstrip('/') + '/mcp')}
bearer_token_env_var = "MESA_CODEX_MCP_TOKEN"
enabled = true
required = false
startup_timeout_sec = 10
tool_timeout_sec = 45
enabled_tools = [{tools}]
default_tools_approval_mode = "writes"

[mcp_servers.mesa.tools.mesa_forget]
approval_mode = "prompt"
{_MANAGED_END}
"""


def _is_mesa_hook(item: Any) -> bool:
    serialized = json.dumps(item)
    return "mesa codex hook" in serialized or ".codex/hooks/mesa_" in serialized


def _managed_hooks() -> dict[str, dict[str, Any]]:
    return {
        "SessionStart": {
            "matcher": "startup|resume|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": "mesa codex hook start",
                    "timeout": 8,
                    "statusMessage": "Loading MESA project memory",
                }
            ],
        },
        "SessionEnd": {
            "hooks": [
                {
                    "type": "command",
                    "command": "mesa codex hook end",
                    "timeout": 2,
                    "statusMessage": "Finalizing MESA session",
                }
            ]
        },
        "PostCompact": {
            "hooks": [
                {
                    "type": "command",
                    "command": "mesa codex hook post-compact",
                    "timeout": 8,
                    "statusMessage": "Refreshing MESA constraints",
                }
            ]
        },
    }


def _optional_bool(value: str | None) -> bool | None:
    return None if value is None else value == "on"


async def _verify_gateway_credential(gateway_url: str, token: str) -> None:
    """Prove a rotated secret can start an actual direct MCP session."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        async with streamable_http_client(
            gateway_url.rstrip("/") + "/mcp", http_client=client
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
