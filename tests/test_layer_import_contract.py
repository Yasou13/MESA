from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_layer_imports import (
    find_new_legal_importers,
    find_reverse_dependencies,
)


def test_production_packages_have_one_way_imports() -> None:
    assert find_reverse_dependencies() == []


def test_layer_guard_detects_a_reverse_dependency(tmp_path: Path) -> None:
    for package in ("mesa_storage", "mesa_memory", "mesa_workers", "mesa_api", "mesa_mcp"):
        (tmp_path / package).mkdir()
    (tmp_path / "mesa_storage" / "bad.py").write_text(
        "from mesa_memory import config\n", encoding="utf-8"
    )

    assert find_reverse_dependencies(tmp_path) == [
        "mesa_storage/bad.py -> mesa_memory"
    ]


def test_production_legal_imports_stay_within_the_allow_list() -> None:
    assert find_new_legal_importers() == []


def test_legal_import_guard_allows_existing_importers_and_ignores_tests(tmp_path: Path) -> None:
    for relative in (
        "mesa_api/v4_router.py",
        "mesa_storage/dao.py",
        "mesa_storage/kuzu_provider.py",
        "mesa_storage/legal_identity.py",
        "mesa_storage/legal_rules.py",
        "tests/test_legal.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from mesa_storage import legal_identity, legal_rules\n", encoding="utf-8")

    assert find_new_legal_importers(tmp_path) == []


@pytest.mark.parametrize(
    ("statement", "module"),
    [
        ("import mesa_storage.legal_rules as rules", "legal_rules"),
        ("from mesa_storage import legal_identity", "legal_identity"),
        ("from mesa_storage.legal_rules import extract_legal_rules", "legal_rules"),
        ("from . import legal_rules", "legal_rules"),
        ("from .legal_identity import LegalEntityResolver", "legal_identity"),
    ],
)
def test_legal_import_guard_blocks_new_importers(tmp_path: Path, statement: str, module: str) -> None:
    path = tmp_path / "mesa_storage/new_importer.py"
    path.parent.mkdir()
    path.write_text(f"{statement}\n", encoding="utf-8")

    assert find_new_legal_importers(tmp_path) == [
        f"mesa_storage/new_importer.py -> mesa_storage.{module}"
    ]
