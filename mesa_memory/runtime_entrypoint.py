"""Validated container process entrypoint for MESA runtime profiles."""

# ruff: noqa: E402 -- logging must be configured before runtime imports.

from __future__ import annotations

import os
import sys

from mesa_memory.observability.logger import setup_logging

setup_logging(role="launcher")

from mesa_memory.config import (
    RuntimeProfile,
    load_explicit_dotenv,
    load_runtime_profile,
    refresh_config_from_environment,
)


def command_for_profile() -> list[str]:
    # Parse only enough to validate the explicit dotenv path, then load and
    # reparse so profile/storage/model decisions use the declared values.
    bootstrap = load_runtime_profile()
    load_explicit_dotenv(bootstrap)
    refresh_config_from_environment()
    runtime = load_runtime_profile()
    if runtime.profile is RuntimeProfile.WORKER_ONLY:
        return [sys.executable, "-m", "mesa_memory.worker_runtime"]
    if not runtime.api_enabled:
        raise RuntimeError("selected runtime profile does not expose an API process")
    port = os.environ.get("MESA_PORT", "8000")
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "mesa_memory.api.server:app",
        "--host",
        "0.0.0.0",
        "--port",
        port,
    ]


def main() -> None:
    command = command_for_profile()
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
