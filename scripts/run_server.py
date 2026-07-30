#!/usr/bin/env python3
"""Deprecated development wrapper for the canonical MESA runtime."""

from __future__ import annotations

import argparse
import os
import warnings
from pathlib import Path

import uvicorn

from mesa_runtime.dashboard import dashboard_static_file


def _dashboard_static_file(dashboard_path: str, full_path: str) -> str | None:
    """Compatibility wrapper for the historical traversal-safe helper."""
    resolved = dashboard_static_file(Path(dashboard_path), full_path)
    return str(resolved) if resolved is not None else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_server",
        description="Deprecated wrapper for mesa_runtime.app:create_app",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=int(os.environ.get("MESA_PORT", "8000")),
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--full", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    warnings.warn(
        "scripts/run_server.py is deprecated; use python -m mesa_runtime.cli",
        DeprecationWarning,
        stacklevel=2,
    )
    os.environ.setdefault(
        "MESA_RUNTIME_PROFILE", "combined" if args.full else "api-only"
    )
    os.environ.setdefault("MESA_ENVIRONMENT", "development")
    os.environ.setdefault("MESA_MODEL_ENABLED", "true" if args.full else "false")
    os.environ.setdefault(
        "MESA_EXTERNAL_PROVIDER_ENABLED", "true" if args.full else "false"
    )
    if args.no_auth:
        os.environ["MESA_ALLOW_UNAUTHENTICATED"] = "true"

    uvicorn.run(
        "mesa_runtime.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
