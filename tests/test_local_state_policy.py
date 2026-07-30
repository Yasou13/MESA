from __future__ import annotations

from pathlib import Path

import pytest
from mesa_memory.config import RuntimeProfile, load_runtime_profile
from mesa_runtime.local_state import (
    LocalStateMigrationError,
    migrate_local_state,
    migration_items,
)


def _runtime_environment(**values: str) -> dict[str, str]:
    return {
        "MESA_RUNTIME_PROFILE": RuntimeProfile.API_ONLY.value,
        **values,
    }


def test_runtime_storage_prefers_explicit_configuration_over_xdg(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    xdg = tmp_path / "xdg"
    runtime = load_runtime_profile(
        _runtime_environment(
            MESA_STORAGE_ROOT=str(explicit),
            XDG_DATA_HOME=str(xdg),
        )
    )

    assert runtime.storage_root == explicit.resolve()


def test_runtime_storage_uses_xdg_without_creating_repository_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    xdg = tmp_path / "xdg"
    monkeypatch.chdir(repository)

    runtime = load_runtime_profile(
        _runtime_environment(XDG_DATA_HOME=str(xdg))
    )

    assert runtime.storage_root == (xdg / "mesa").resolve()
    assert not (repository / "storage").exists()
    assert not runtime.storage_root.exists()


def test_legacy_storage_fallback_is_read_only_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    legacy = repository / "storage"
    legacy.mkdir(parents=True)
    xdg = tmp_path / "xdg"
    monkeypatch.chdir(repository)

    with pytest.warns(DeprecationWarning, match="legacy repository-local"):
        runtime = load_runtime_profile(
            _runtime_environment(XDG_DATA_HOME=str(xdg))
        )

    assert runtime.storage_root == legacy.resolve()
    assert not (xdg / "mesa").exists()


def test_local_state_migration_is_dry_run_by_default(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "storage"
    source.mkdir(parents=True)
    (source / "mesa.db").write_text("fixture", encoding="utf-8")
    xdg = tmp_path / "xdg"

    items = migrate_local_state(
        repository,
        environ={"XDG_DATA_HOME": str(xdg)},
    )

    runtime = next(item for item in items if item.name == "runtime-storage")
    assert runtime.status == "ready"
    assert not runtime.destination.exists()
    assert (source / "mesa.db").is_file()


def test_local_state_migration_copies_without_deleting_source(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    source = repository / "storage"
    source.mkdir(parents=True)
    (source / "mesa.db").write_text("fixture", encoding="utf-8")
    xdg = tmp_path / "xdg"

    items = migrate_local_state(
        repository,
        apply=True,
        environ={"XDG_DATA_HOME": str(xdg)},
    )

    runtime = next(item for item in items if item.name == "runtime-storage")
    assert runtime.status == "copied"
    assert (runtime.destination / "mesa.db").read_text(encoding="utf-8") == "fixture"
    assert (source / "mesa.db").is_file()


def test_local_state_migration_never_overwrites_destination(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    source = repository / "storage"
    source.mkdir(parents=True)
    (source / "mesa.db").write_text("source", encoding="utf-8")
    destination = tmp_path / "xdg" / "mesa"
    destination.mkdir(parents=True)
    (destination / "mesa.db").write_text("destination", encoding="utf-8")

    items = migrate_local_state(
        repository,
        apply=True,
        environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
    )

    runtime = next(item for item in items if item.name == "runtime-storage")
    assert runtime.status == "destination-exists"
    assert (destination / "mesa.db").read_text(encoding="utf-8") == "destination"


def test_local_state_migration_rejects_symlinked_sources(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "storage"
    source.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("private", encoding="utf-8")
    (source / "linked").symlink_to(outside)

    with pytest.raises(LocalStateMigrationError, match="symlink"):
        migrate_local_state(
            repository,
            apply=True,
            environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
        )


def test_migration_plan_covers_runtime_and_benchmark_state(
    tmp_path: Path,
) -> None:
    names = {
        item.name
        for item in migration_items(
            tmp_path,
            {
                "XDG_DATA_HOME": str(tmp_path / "data"),
                "XDG_STATE_HOME": str(tmp_path / "state"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
            },
        )
    }

    assert names == {
        "runtime-storage",
        "runtime-results",
        "benchmark-cache",
        "benchmark-datasets",
    }
