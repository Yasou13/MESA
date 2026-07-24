"""Small, conservative validation helpers for the MCP boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import MCPError

MEMORY_TYPES = frozenset(
    {
        "fact",
        "preference",
        "decision",
        "architecture",
        "constraint",
        "convention",
        "error",
        "solution",
        "task",
        "summary",
        "relationship",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\b(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+", re.IGNORECASE),
)


def reject_secrets(value: Any) -> None:
    """Reject likely credentials in content or metadata before persistence."""
    serialised = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    if any(pattern.search(serialised) for pattern in _SECRET_PATTERNS):
        raise MCPError("INVALID_ARGUMENT", "content or metadata appears to contain a secret")


def validate_source_file(source_file: str | None, workspace_root: Path) -> str | None:
    if source_file is None:
        return None
    candidate = Path(source_file)
    resolved = (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise MCPError("INVALID_ARGUMENT", "source_file must be inside MESA_WORKSPACE_ROOT") from exc
    return relative.as_posix()
