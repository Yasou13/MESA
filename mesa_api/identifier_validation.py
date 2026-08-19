"""Shared public identifier constraints for versioned API boundaries."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field

_RESERVED_SENTINELS = frozenset({"__unset__", "__system__", ""})
_ALLOWED_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def validate_public_identifier(
    value: str,
    field_name: str = "identifier",
    *,
    max_length: int = 128,
    restricted_charset: bool = True,
) -> str:
    """Validate one caller-controlled public identifier.

    The 128-character form covers tenant, workspace, dataset, agent, session,
    mutation, and operation identifiers.  Source-owned document, revision, and
    chunk identifiers retain their established 256-character boundary.
    """
    if _CONTROL_CHAR_PATTERN.search(value):
        raise ValueError(
            f"{field_name} contains illegal control characters. "
            "Only printable ASCII characters are allowed."
        )

    stripped = value.strip()
    if stripped in _RESERVED_SENTINELS:
        raise ValueError(f"{field_name} cannot be empty or a reserved value")
    if len(stripped) > max_length:
        raise ValueError(f"{field_name} must be between 1 and {max_length} characters")
    if restricted_charset and not _ALLOWED_IDENTIFIER_PATTERN.fullmatch(stripped):
        raise ValueError(
            f"{field_name} may contain only alphanumeric characters, hyphens, "
            "underscores, or dots"
        )
    return stripped


def _validate_standard_identifier(value: str) -> str:
    return validate_public_identifier(value)


def _validate_source_identifier(value: str) -> str:
    # Document/revision/chunk aliases historically permit external naming
    # punctuation.  Preserve that compatibility while enforcing the shared
    # control-character, sentinel, and size boundaries.
    return validate_public_identifier(value, max_length=256, restricted_charset=False)


PublicIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(_validate_standard_identifier),
]
SourceIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=256),
    AfterValidator(_validate_source_identifier),
]
