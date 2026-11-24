"""Shared ISO-8601 temporal parsing and UTC normalization helpers (D85)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

_TEMPORAL_KEYS = ("valid_from", "valid_to")


def parse_iso8601_temporal(value: Any) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp string or datetime.

    Rejects date-only strings, naive timestamps, control characters, and
    non-string/non-datetime values.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "temporal value must include a timezone offset (e.g. 'Z' or '+00:00')"
            )
        return value
    if not isinstance(value, str):
        raise ValueError("temporal value must be a valid ISO-8601 timestamp string")
    raw = value.strip()
    if not raw or len(raw) > 128 or any(ord(char) < 32 for char in raw):
        raise ValueError("temporal value must be a valid ISO-8601 timestamp string")
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "temporal value must be a valid ISO-8601 timestamp string"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            "temporal value must include a timezone offset (e.g. 'Z' or '+00:00')"
        )
    return parsed


def normalize_utc_iso8601(value: Any) -> str:
    """Validate an ISO-8601 timestamp with offset and format it in UTC ('...Z')."""
    parsed = parse_iso8601_temporal(value)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_temporal_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and UTC-normalize optional valid_from / valid_to metadata fields.

    Enforces D85: ISO-8601 timestamps with a timezone offset, and strictly
    valid_from < valid_to when both are present.
    """
    if "valid_from" not in metadata and "valid_to" not in metadata:
        return dict(metadata) if not isinstance(metadata, dict) else metadata
    parsed: dict[str, datetime] = {}
    normalized_values: dict[str, str] = {}
    for key in _TEMPORAL_KEYS:
        if key not in metadata or metadata[key] is None:
            continue
        try:
            dt = parse_iso8601_temporal(metadata[key])
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
        parsed[key] = dt
        normalized_values[key] = (
            dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    if (
        "valid_from" in parsed
        and "valid_to" in parsed
        and parsed["valid_from"] >= parsed["valid_to"]
    ):
        raise ValueError("valid_from must be before valid_to")
    if not normalized_values:
        return dict(metadata) if not isinstance(metadata, dict) else metadata
    result = dict(metadata)
    result.update(normalized_values)
    return result
