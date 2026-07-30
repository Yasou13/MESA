"""Versioned contracts must remain independent of runtime implementations."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from mesa_contracts.v3 import MemoryInsertRequest
from mesa_contracts.v4 import V4MemoryInsertRequest


def test_legacy_v3_schema_path_reexports_identical_contract_types() -> None:
    legacy = importlib.import_module("mesa_api.schemas")
    assert legacy.MemoryInsertRequest is MemoryInsertRequest


def test_v4_router_reexports_identical_contract_types_during_transition() -> None:
    legacy = importlib.import_module("mesa_api.v4_router")
    assert legacy.V4MemoryInsertRequest is V4MemoryInsertRequest


def test_contract_package_has_no_production_implementation_imports() -> None:
    root = Path(__file__).parents[1] / "mesa_contracts"
    forbidden = {
        "mesa_api",
        "mesa_client",
        "mesa_mcp",
        "mesa_memory",
        "mesa_runtime",
        "mesa_storage",
        "mesa_workers",
    }
    imports: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
    assert imports.isdisjoint(forbidden)
