"""Container health probe for API and worker runtime profiles."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from mesa_memory.config import RuntimeProfile, load_runtime_profile


def worker_is_ready(storage_root: Path, *, max_age_seconds: float = 90.0) -> bool:
    try:
        payload = json.loads(
            (storage_root / "worker-readiness.json").read_text(encoding="utf-8")
        )
        updated = datetime.fromisoformat(str(payload["updated_at"]))
        age = (
            datetime.now(timezone.utc) - updated.astimezone(timezone.utc)
        ).total_seconds()
        return payload.get("status") == "RUNNING" and 0 <= age <= max_age_seconds
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def main() -> int:
    runtime = load_runtime_profile()
    if runtime.profile is RuntimeProfile.WORKER_ONLY:
        return 0 if worker_is_ready(runtime.storage_root) else 1
    port = int(os.environ.get("MESA_PORT", "8000"))
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health/init", timeout=3
        ) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
