"""Stage generated frontend assets for Python package builds."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).parents[1]
CONTROL_DIST = ROOT / "apps" / "control-dashboard" / "dist"
CONTROL_STAGE = (
    ROOT
    / "packages"
    / "mesa-memory"
    / "src"
    / "mesa_runtime"
    / "static"
    / "dashboard"
)
BENCHMARK_STAGE = (
    ROOT
    / "packages"
    / "mesa-benchmark"
    / "src"
    / "mesa_benchmark"
    / "dashboard"
    / "static"
)


def stage_directory(source: Path, destination: Path) -> None:
    if not (source / "index.html").is_file():
        raise SystemExit(f"frontend build is missing: {source / 'index.html'}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def main() -> None:
    stage_directory(CONTROL_DIST, CONTROL_STAGE)
    if not (BENCHMARK_STAGE / "index.html").is_file():
        raise SystemExit(
            f"benchmark frontend build is missing: {BENCHMARK_STAGE / 'index.html'}"
        )


if __name__ == "__main__":
    main()
