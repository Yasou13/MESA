from __future__ import annotations

import pytest

from mesa_mcp.errors import MCPError
from mesa_mcp.gateway.codex_transport import _tools as codex_tools
from mesa_mcp.gateway.operations import _remember_provenance


def test_remember_provenance_is_validated_and_forwarded() -> None:
    provenance = _remember_provenance(
        {
            "metadata": {"source_system": "architecture-review"},
            "source_ref": "meeting://architecture/2026-07-28",
            "evidence_span": "paragraphs 2-4",
            "memory_type": "decision",
            "importance": 0.9,
        }
    )

    assert provenance == {
        "metadata": {
            "source_system": "architecture-review",
            "memory_type": "decision",
            "importance": 0.9,
        },
        "source_ref": "meeting://architecture/2026-07-28",
        "evidence_span": "paragraphs 2-4",
    }


def test_remember_provenance_rejects_invalid_importance() -> None:
    with pytest.raises(MCPError, match="importance"):
        _remember_provenance({"importance": True})


def test_codex_remember_schema_exposes_optional_provenance() -> None:
    remember = next(tool for tool in codex_tools() if tool.name == "mesa_remember")
    properties = remember.inputSchema["properties"]

    assert {"source_ref", "evidence_span", "memory_type", "importance"} <= set(
        properties
    )
