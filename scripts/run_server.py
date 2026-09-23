#!/usr/bin/env python3
"""Thin development launcher for the canonical MESA runtime composition."""

from __future__ import annotations

import argparse
import os

import uvicorn

from mesa_memory.api import server as _server

app = _server.app
_state = _server.state


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
