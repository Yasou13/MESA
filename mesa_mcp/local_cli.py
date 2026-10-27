"""Local CLI tools for MESA (D27, D30).

Provides subcommands `start`, `add`, `search`, `delete`, `status`, `mcp-config`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl


def resolve_local_paths(
    home: Path | None = None,
    storage_root: Path | None = None,
    port: int | None = None,
) -> tuple[Path, Path, int]:
    resolved_home = (
        home
        or (Path(os.environ["MESA_HOME"]).expanduser() if "MESA_HOME" in os.environ else Path.home() / ".mesa")
    )
    resolved_storage = (
        storage_root
        or (Path(os.environ["MESA_STORAGE_ROOT"]).expanduser() if "MESA_STORAGE_ROOT" in os.environ else resolved_home / "data")
    )
    resolved_port = port or int(os.environ.get("MESA_PORT", 8000))
    return resolved_home, resolved_storage, resolved_port


async def provision_credentials(
    home: Path,
    storage_root: Path,
    port: int = 8000,
) -> dict[str, Any]:
    creds_path = home / "credentials.json"
    policy_path = storage_root / "rbac_policy.db"

    if creds_path.is_file() and policy_path.is_file():
        try:
            with creds_path.open("r", encoding="utf-8") as f:
                creds = json.load(f)
            api_key = creds.get("api_key")
            if api_key:
                key_store = APIKeyStore(str(policy_path))
                await key_store.initialize()
                verified = await key_store.verify(api_key)
                if verified is not None:
                    print(f"Using existing active credentials from {creds_path}")
                    return creds
        except Exception:
            pass

    home.mkdir(parents=True, exist_ok=True)
    storage_root.mkdir(parents=True, exist_ok=True)

    key_store = APIKeyStore(str(policy_path))
    access = AccessControl(str(policy_path))
    await key_store.initialize()
    await access.initialize()

    credential = await key_store.issue_key(
        principal_id="local-admin",
        principal_type="SERVICE",
    )
    await access.grant_scope_role("local-admin", tenant_id="local", role="OWNER")
    await access.grant_principal_permission(
        "local-admin", "local-agent", "SESSION_CREATE"
    )
    await access.grant_dataset_permission(
        "local-admin", tenant_id="local", dataset_id="default", permission="PURGE"
    )
    await access.grant_dataset_permission(
        "local-admin", tenant_id="local", dataset_id="default", permission="ROLLBACK"
    )

    creds = {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key": credential,
        "tenant_id": "local",
        "workspace_id": "default",
        "dataset_id": "default",
        "agent_id": "local-agent",
    }

    # Write credentials.json securely with mode 0600
    fd = os.open(creds_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)
    os.chmod(creds_path, 0o600)

    print(f"Credentials written to {creds_path}")
    return creds


def start_command(args: argparse.Namespace) -> None:
    if sys.platform == "win32":
        print("MESA runs on Linux and macOS; on Windows use Docker or WSL")
        sys.exit(1)

    home, storage_root, port = resolve_local_paths(
        home=getattr(args, "home", None),
        storage_root=getattr(args, "storage_root", None),
        port=getattr(args, "port", None),
    )

    asyncio.run(provision_credentials(home, storage_root, port))

    os.environ["MESA_RUNTIME_PROFILE"] = "combined"
    os.environ["MESA_STORAGE_ROOT"] = str(storage_root)
    if not any(
        var in os.environ
        for var in ("MESA_MODEL_ENABLED", "MESA_LLM_MODEL", "MESA_EMBEDDING_MODEL")
    ):
        os.environ["MESA_MODEL_ENABLED"] = "false"

    env_file = home / ".env"
    if env_file.is_file():
        os.environ["MESA_LOAD_DOTENV"] = "true"
        os.environ["MESA_DOTENV_PATH"] = str(env_file.resolve())

    import uvicorn

    uvicorn.run("mesa_memory.api.server:app", host="127.0.0.1", port=port, log_level="info")


def add_command(args: argparse.Namespace) -> None:
    if args.file == "-":
        content = sys.stdin.read()
    else:
        file_path = Path(args.file)
        if not file_path.is_file():
            sys.stderr.write(f"File not found: {args.file}\n")
            sys.exit(1)
        content = file_path.read_text(encoding="utf-8")

    from mesa_client import Mesa

    mesa = Mesa()
    try:
        res = mesa.add(content, source=args.source, title=args.title)
    finally:
        mesa.close()

    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        doc_id = res.get("document_id")
        chunks = res.get("chunk_count", 0)
        dup = res.get("duplicate", False)
        dup_str = " (duplicate)" if dup else ""
        print(f"Added document {doc_id} with {chunks} chunk(s){dup_str}")


def search_command(args: argparse.Namespace) -> None:
    from mesa_client import Mesa

    mesa = Mesa()
    try:
        results = mesa.search(args.query, limit=args.limit)
    finally:
        mesa.close()

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No matching memories found.")
            return
        for i, item in enumerate(results, start=1):
            score = item.get("score", 0.0)
            doc_id = item.get("document_id") or item.get("chunk_id") or "unknown"
            content = (item.get("content") or "").strip()
            print(f"[{i}] score: {score:.3f} | {doc_id}")
            if content:
                for line in content.splitlines():
                    print(f"    {line}")


def delete_command(args: argparse.Namespace) -> None:
    from mesa_client import Mesa

    mesa = Mesa()
    try:
        res = mesa.delete(args.document_id)
    finally:
        mesa.close()

    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
    else:
        print(f"Purged document {args.document_id}")


def status_command(args: argparse.Namespace) -> None:
    home, _, _ = resolve_local_paths()
    creds_path = home / "credentials.json"
    if not creds_path.is_file():
        sys.stderr.write(
            f"No credentials found at {creds_path}. Run 'mesa start' first.\n"
        )
        sys.exit(1)

    try:
        with creds_path.open("r", encoding="utf-8") as f:
            creds = json.load(f)
    except Exception as exc:
        sys.stderr.write(f"Failed to read credentials: {exc}\n")
        sys.exit(1)

    base_url = (
        os.environ.get("MESA_BASE_URL")
        or creds.get("base_url")
        or "http://127.0.0.1:8000"
    ).rstrip("/")
    api_key = creds.get("api_key")

    try:
        with httpx.Client(timeout=5.0) as client:
            init_resp = client.get(f"{base_url}/health/init")
            init_data = (
                init_resp.json()
                if init_resp.status_code == 200
                else {"status": "unhealthy", "code": init_resp.status_code}
            )

            headers = {"X-API-Key": api_key} if api_key else {}
            health_resp = client.get(f"{base_url}/health", headers=headers)
            health_data = (
                health_resp.json()
                if health_resp.status_code == 200
                else {"status": "unhealthy", "code": health_resp.status_code}
            )
    except Exception as exc:
        sys.stderr.write(f"Failed to connect to MESA at {base_url}: {exc}\n")
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps({"init": init_data, "health": health_data}, indent=2))
    else:
        init_status = init_data.get("status", "unknown")
        health_status = health_data.get("status", "unknown")
        print(f"MESA Server: {base_url}")
        print(f"Readiness (/health/init): {init_status}")
        print(f"Health (/health): {health_status}")


def mcp_config_command(args: argparse.Namespace) -> None:
    home, _, _ = resolve_local_paths()
    creds_path = home / "credentials.json"
    creds: dict[str, Any] = {}
    if creds_path.is_file():
        try:
            with creds_path.open("r", encoding="utf-8") as f:
                creds = json.load(f)
        except Exception:
            pass

    cmd = shutil.which("mesa-mcp")
    if not cmd:
        venv_candidate = Path(sys.executable).parent / "mesa-mcp"
        if venv_candidate.is_file():
            cmd = str(venv_candidate.resolve())
        else:
            cmd = "mesa-mcp"
    else:
        cmd = str(Path(cmd).resolve())

    sys.stderr.write(
        "Warning: the configuration below contains your MESA API key in plaintext.\n"
    )

    config = {
        "mcpServers": {
            "mesa": {
                "command": cmd,
                "env": {
                    "MESA_BASE_URL": creds.get("base_url", "http://127.0.0.1:8000"),
                    "MESA_API_KEY": creds.get("api_key", ""),
                    "MESA_TENANT_ID": creds.get("tenant_id", "local"),
                    "MESA_WORKSPACE_ID": creds.get("workspace_id", "default"),
                    "MESA_DATASET_ID": creds.get("dataset_id", "default"),
                    "MESA_ACTOR_ID": creds.get("agent_id", "local-agent"),
                },
            }
        }
    }
    print(json.dumps(config, indent=2))


def register_local_commands(subparsers: argparse._SubParsersAction) -> None:
    start_parser = subparsers.add_parser(
        "start", help="start the local MESA server"
    )
    start_parser.add_argument("--port", type=int, default=None, help="port to bind (default: 8000)")
    start_parser.add_argument("--home", type=Path, default=None, help="MESA home directory (default: ~/.mesa)")
    start_parser.add_argument("--storage-root", type=Path, default=None, help="storage root directory")

    add_parser = subparsers.add_parser(
        "add", help="add a document to MESA"
    )
    add_parser.add_argument("file", help="path to text file or '-' for stdin")
    add_parser.add_argument("--source", required=True, help="source reference URI (e.g. file://path)")
    add_parser.add_argument("--title", default=None, help="document title")
    add_parser.add_argument("--json", action="store_true", help="output JSON")

    search_parser = subparsers.add_parser(
        "search", help="search MESA memory"
    )
    search_parser.add_argument("query", help="search query")
    search_parser.add_argument("--limit", type=int, default=10, help="maximum results (default: 10)")
    search_parser.add_argument("--json", action="store_true", help="output JSON")

    delete_parser = subparsers.add_parser(
        "delete", help="purge a document from MESA"
    )
    delete_parser.add_argument("document_id", help="document ID to purge")
    delete_parser.add_argument("--json", action="store_true", help="output JSON")

    status_parser = subparsers.add_parser(
        "status", help="check server health and readiness"
    )
    status_parser.add_argument("--json", action="store_true", help="output JSON")

    subparsers.add_parser(
        "mcp-config", help="print MCP client configuration JSON"
    )


def handle_local_command(args: argparse.Namespace) -> None:
    handlers = {
        "start": start_command,
        "add": add_command,
        "search": search_command,
        "delete": delete_command,
        "status": status_command,
        "mcp-config": mcp_config_command,
    }
    handler = handlers.get(args.group)
    if handler:
        handler(args)
    else:
        raise ValueError(f"Unknown local command: {args.group}")
