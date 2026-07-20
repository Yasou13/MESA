"""Validated container process entrypoint for MESA runtime profiles."""
from __future__ import annotations

import os

from mesa_memory.config import RuntimeProfile, load_runtime_profile


def command_for_profile() -> list[str]:
    runtime = load_runtime_profile()
    if runtime.profile is RuntimeProfile.WORKER_ONLY:
        return ["python", "-m", "mesa_memory.worker_runtime"]
    if not runtime.api_enabled:
        raise RuntimeError("selected runtime profile does not expose an API process")
    port = os.environ.get("MESA_PORT", "8000")
    return ["uvicorn", "mesa_memory.api.server:app", "--host", "0.0.0.0", "--port", port]


def main() -> None:
    command = command_for_profile()
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
