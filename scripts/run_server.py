#!/usr/bin/env python3
"""Thin development launcher for the canonical MESA runtime composition."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import unquote

import uvicorn

from mesa_memory.api import server as _server

app = _server.app
_state = _server.state


def _dashboard_static_file(dashboard_path: str, full_path: str) -> str | None:
    """Return an existing dashboard file only when it remains under ``dist``."""
    if not full_path:
        return None
    dashboard_root = Path(dashboard_path).resolve()
    requested_path = (dashboard_root / unquote(full_path)).resolve()
    if not requested_path.is_relative_to(dashboard_root):
        return None
    if requested_path.is_dir():
        requested_path = (requested_path / "index.html").resolve()
    if (
        not requested_path.is_relative_to(dashboard_root)
        or not requested_path.is_file()
    ):
        return None
    return str(requested_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_server",
        description="Launch the canonical MESA API runtime",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=int(os.environ.get("MESA_PORT", "8000")),
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    uvicorn.run(
        "mesa_memory.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
