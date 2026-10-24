"""Container health probe for the combined runtime."""

from __future__ import annotations

import os
import sys
import urllib.request

from mesa_memory.config import load_runtime_profile


def main() -> int:
    load_runtime_profile()
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
