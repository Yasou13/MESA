"""Stable workspace identifiers shared by MCP transports and provisioning."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def workspace_fingerprint(root: Path) -> str:
    identity = str(root.resolve())
    try:
        remote = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if remote:
            identity = remote
    except OSError:
        pass
    return f"sha256:{hashlib.sha256(identity.encode()).hexdigest()}"
