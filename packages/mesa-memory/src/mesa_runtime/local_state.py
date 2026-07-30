"""Explicit, non-destructive migration of repository-local MESA state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


class LocalStateMigrationError(RuntimeError):
    """A migration cannot proceed without risking an unsafe copy."""


@dataclass(frozen=True)
class MigrationItem:
    name: str
    source: Path
    destination: Path
    status: str


def _xdg_root(
    environ: Mapping[str, str], variable: str, fallback: Path
) -> Path:
    return Path(environ.get(variable, str(fallback))).expanduser().resolve(
        strict=False
    )


def migration_items(
    repository: Path,
    environ: Mapping[str, str] | None = None,
) -> list[MigrationItem]:
    """Describe legacy sources and their XDG destinations without writing."""
    values = os.environ if environ is None else environ
    repository = repository.expanduser().resolve(strict=False)
    data = _xdg_root(values, "XDG_DATA_HOME", Path.home() / ".local" / "share")
    state = _xdg_root(values, "XDG_STATE_HOME", Path.home() / ".local" / "state")
    cache = _xdg_root(values, "XDG_CACHE_HOME", Path.home() / ".cache")
    candidates = (
        ("runtime-storage", repository / "storage", data / "mesa"),
        ("runtime-results", repository / "results", state / "mesa" / "results"),
        (
            "benchmark-cache",
            repository / "mesa-benchmark" / ".cache",
            cache / "mesa" / "benchmark",
        ),
        (
            "benchmark-datasets",
            repository / "mesa-benchmark" / "datasets",
            data / "mesa" / "benchmark" / "datasets",
        ),
    )
    items: list[MigrationItem] = []
    for name, source, destination in candidates:
        if not source.exists():
            status = "source-missing"
        elif destination.exists():
            status = "destination-exists"
        else:
            status = "ready"
        items.append(MigrationItem(name, source, destination, status))
    return items


def _reject_symlinks(source: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise LocalStateMigrationError(
            f"legacy source contains a symlink and was not copied: {source}"
        )


def migrate_local_state(
    repository: Path,
    *,
    apply: bool = False,
    environ: Mapping[str, str] | None = None,
) -> list[MigrationItem]:
    """Copy ready sources when requested; never overwrite or remove originals."""
    items = migration_items(repository, environ)
    if not apply:
        return items
    migrated: list[MigrationItem] = []
    for item in items:
        if item.status != "ready":
            migrated.append(item)
            continue
        _reject_symlinks(item.source)
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(item.source, item.destination)
        except FileExistsError:
            migrated.append(
                MigrationItem(
                    item.name,
                    item.source,
                    item.destination,
                    "destination-exists",
                )
            )
        else:
            migrated.append(
                MigrationItem(
                    item.name,
                    item.source,
                    item.destination,
                    "copied",
                )
            )
    return migrated


def _serialise(items: Sequence[MigrationItem]) -> str:
    return json.dumps(
        [
            {
                **asdict(item),
                "source": str(item.source),
                "destination": str(item.destination),
            }
            for item in items
        ],
        indent=2,
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy legacy repository-local MESA state to XDG directories. "
            "Sources are never deleted and destinations are never overwritten."
        )
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="legacy repository root (default: current directory)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform safe copies; without this flag the command is a dry-run",
    )
    args = parser.parse_args(argv)
    try:
        items = migrate_local_state(args.repository, apply=args.apply)
    except LocalStateMigrationError as exc:
        parser.error(str(exc))
    print(_serialise(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
