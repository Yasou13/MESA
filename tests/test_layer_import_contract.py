from __future__ import annotations

from pathlib import Path

from scripts.check_layer_imports import find_reverse_dependencies


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
