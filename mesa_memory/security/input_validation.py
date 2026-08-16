"""Shared fail-closed validation for write payloads at API boundaries."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_METADATA_BYTES = 16 * 1024
MAX_METADATA_DEPTH = 2

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(
        r"\b(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


def validate_write_payload(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Reject secrets and unbounded metadata before any durable write."""
    encoded = json.dumps(metadata, sort_keys=True, default=str).encode()
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds 16 KB")
    _validate_metadata(metadata, depth=1)
    serialised = content + "\n" + encoded.decode(errors="replace")
    if any(pattern.search(serialised) for pattern in _SECRET_PATTERNS):
        raise ValueError("content or metadata appears to contain a secret")
    return metadata


def _validate_metadata(value: dict[str, Any], *, depth: int) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError("metadata nesting exceeds the supported depth")
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if key.startswith("_mesa_"):
            raise ValueError("metadata keys beginning with '_mesa_' are reserved")
        if isinstance(item, dict):
            _validate_metadata(item, depth=depth + 1)
        elif isinstance(item, list):
            if depth >= MAX_METADATA_DEPTH or any(
                isinstance(entry, (dict, list)) for entry in item
            ):
                raise ValueError("metadata nesting exceeds the supported depth")
