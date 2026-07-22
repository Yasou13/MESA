"""Portable path resolution for source checkouts and installed wheels."""

from __future__ import annotations

import os
import warnings
from importlib.resources import files
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = PACKAGE_ROOT.parent
REPOSITORY_ROOT = BENCHMARK_ROOT.parent


CONFIG_ALIASES = {
    "config.yaml": "resource://configs/legacy/default.yaml",
    "config_beam.yaml": "resource://configs/release/beam_128k.yaml",
    "config_beam_10m_capacity.yaml": "resource://configs/research/beam_10m_capacity.yaml",
    "config_beam_1m.yaml": "resource://configs/research/beam_1m.yaml",
    "config_beam_500k.yaml": "resource://configs/research/beam_500k.yaml",
    "config_beam_512_64.yaml": "resource://configs/research/beam_512_64.yaml",
    "config_contradiction.yaml": "resource://configs/internal/contradiction_v3.yaml",
    "config_holdout.yaml": "resource://configs/internal/holdout_600.yaml",
    "config_letta.yaml": "resource://configs/legacy/letta.yaml",
    "config_locomo.yaml": "resource://configs/research/locomo.yaml",
    "config_longmemeval.yaml": "resource://configs/release/longmemeval.yaml",
    "config_mem0.yaml": "resource://configs/legacy/mem0.yaml",
    "config_memoryagentbench.yaml": "resource://configs/release/memoryagentbench.yaml",
    "config_memoryagentbench_recsys.yaml": "resource://configs/research/memoryagentbench_recsys.yaml",
    "config_mini_mem0.yaml": "resource://configs/legacy/mini_mem0.yaml",
    "config_mini_mesa.yaml": "resource://configs/legacy/mini_mesa.yaml",
    "config_multi_hop.yaml": "resource://configs/internal/multi_hop_graph.yaml",
    "config_multi_hop_raw.yaml": "resource://configs/internal/multi_hop_raw.yaml",
    "config_reranking.yaml": "resource://configs/legacy/reranking.yaml",
    "config_smoke_dense.yaml": "resource://configs/internal/smoke_dense.yaml",
    "config_zep.yaml": "resource://configs/legacy/zep.yaml",
}


PATH_ALIASES = {
    "mesa-benchmark/mesa_benchmark/datasets/mini_dataset.json": "resource://fixtures/internal/mini_dataset.json",
    "mesa-benchmark/mesa_benchmark/datasets/comprehensive_200_dataset.json": "resource://fixtures/legacy/comprehensive_200_dataset.json",
    "mesa-benchmark/mesa_benchmark/datasets/stress_dataset.json": "resource://fixtures/legacy/stress_dataset.json",
    "mesa-benchmark/datasets/contradiction_v3.json": "resource://fixtures/internal/contradiction_v3.json",
    "mesa-benchmark/datasets/internal_holdout_600.json": "resource://fixtures/internal/internal_holdout_600.json",
    "mesa-benchmark/datasets/comprehensive_multihop_raw_v2.json": "resource://fixtures/internal/comprehensive_multihop_raw_v2.json",
    "mesa-benchmark/datasets/comprehensive_multihop_only.json": "resource://fixtures/internal/comprehensive_multihop_only.json",
    "mesa-benchmark/datasets/contradiction_200.json": "resource://fixtures/legacy/contradiction_200.json",
    "mesa-benchmark/datasets/beam/dataset.json": "data://legacy/beam/v1/dataset.json",
    "mesa-benchmark/datasets/beam/v2/dataset.json": "data://external/beam/v2/dataset.json",
    "mesa-benchmark/datasets/beam/scale/500k.json": "data://generated/beam/scale/500k.json",
    "mesa-benchmark/datasets/beam/scale/1m.json": "data://generated/beam/scale/1m.json",
    "mesa-benchmark/datasets/beam/scale/10m-capacity.json": "data://generated/beam/scale/10m-capacity.json",
    "mesa-benchmark/datasets/beam/ablations/512-64.json": "data://generated/beam/ablations/512-64.json",
    "mesa-benchmark/datasets/locomo/dataset.json": "data://external/locomo/dataset.json",
    "mesa-benchmark/datasets/longmemeval/dataset.json": "data://external/longmemeval/dataset.json",
    "mesa-benchmark/datasets/memoryagentbench/dataset.json": "data://external/memoryagentbench/dataset.json",
    "mesa-benchmark/datasets/memoryagentbench/recsys.json": "data://external/memoryagentbench/recsys.json",
    "mesa-benchmark/datasets/beam/v2/manifest.json": "resource://manifests/external/beam-v2.json",
    "mesa-benchmark/datasets/beam/scale/500k-manifest.json": "resource://manifests/external/beam-500k.json",
    "mesa-benchmark/datasets/beam/scale/1m-manifest.json": "resource://manifests/external/beam-1m.json",
    "mesa-benchmark/datasets/beam/scale/10m-capacity-manifest.json": "data://generated/beam/scale/10m-capacity-manifest.json",
    "mesa-benchmark/datasets/beam/ablations/512-64-manifest.json": "data://generated/beam/ablations/512-64-manifest.json",
    "mesa-benchmark/datasets/locomo/manifest.json": "resource://manifests/external/locomo.json",
    "mesa-benchmark/datasets/longmemeval/manifest.json": "resource://manifests/external/longmemeval.json",
    "mesa-benchmark/datasets/memoryagentbench/manifest.json": "resource://manifests/external/memoryagentbench.json",
    "mesa-benchmark/datasets/memoryagentbench/recsys-manifest.json": "resource://manifests/external/memoryagentbench-recsys.json",
    "mesa-benchmark/datasets/manifests/comprehensive-v2.json": "resource://manifests/internal/comprehensive-v2.json",
    "mesa-benchmark/datasets/manifests/contradiction-v2.json": "resource://manifests/internal/contradiction-v2.json",
    "mesa-benchmark/datasets/manifests/contradiction-v3.json": "resource://manifests/internal/contradiction-v3.json",
    "mesa-benchmark/datasets/manifests/internal-holdout-600.json": "resource://manifests/internal/internal-holdout-600.json",
    "mesa-benchmark/datasets/manifests/mini-v1.json": "resource://manifests/internal/mini-v1.json",
    "mesa-benchmark/datasets/manifests/multihop-raw-v2.json": "resource://manifests/internal/multihop-raw-v2.json",
    "mesa-benchmark/datasets/manifests/multihop-v2.json": "resource://manifests/internal/multihop-v2.json",
}


def resource_root() -> Path:
    return Path(str(files("mesa_benchmark").joinpath("resources"))).resolve()


def is_source_checkout() -> bool:
    return (BENCHMARK_ROOT / "datasets").is_dir() and (
        BENCHMARK_ROOT / "scripts"
    ).is_dir()


def data_root() -> Path:
    if configured := os.environ.get("MESA_BENCHMARK_DATA_DIR"):
        return Path(configured).expanduser().resolve()
    if is_source_checkout():
        return (BENCHMARK_ROOT / "datasets").resolve()
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return (cache_home / "mesa-benchmark" / "datasets").resolve()


def cache_root() -> Path:
    if configured := os.environ.get("MESA_BENCHMARK_CACHE_DIR"):
        return Path(configured).expanduser().resolve()
    if is_source_checkout():
        return (BENCHMARK_ROOT / ".cache").resolve()
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return (cache_home / "mesa-benchmark" / "downloads").resolve()


def resolve_results_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser().resolve()
    if configured := os.environ.get("MESA_BENCHMARK_RESULTS_DIR"):
        return Path(configured).expanduser().resolve()
    if is_source_checkout():
        return (REPOSITORY_ROOT / "results").resolve()
    return (Path.cwd() / "results").resolve()


def _normalise(value: str | Path) -> str:
    return str(value).replace("\\", "/").lstrip("./")


def resolve_benchmark_path(
    value: str | Path,
    *,
    base_dir: str | Path | None = None,
    must_exist: bool = False,
) -> Path:
    raw = str(value)
    normalised = _normalise(raw)
    if normalised in PATH_ALIASES:
        raw = PATH_ALIASES[normalised]
    if raw.startswith("resource://"):
        resolved = resource_root() / raw.removeprefix("resource://")
    elif raw.startswith("data://"):
        resolved = data_root() / raw.removeprefix("data://")
    else:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            resolved = candidate
        else:
            base = Path(base_dir) if base_dir is not None else Path.cwd()
            local = base / candidate
            repository_relative = REPOSITORY_ROOT / candidate
            resolved = (
                local
                if local.exists() or not repository_relative.exists()
                else repository_relative
            )
    resolved = resolved.resolve()
    if must_exist and not resolved.exists():
        raise ValueError(f"benchmark path not found: {raw} (resolved to {resolved})")
    return resolved


def resolve_config_path(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.exists():
        return candidate.resolve()
    alias = CONFIG_ALIASES.get(candidate.name)
    if alias is not None:
        warnings.warn(
            f"legacy benchmark config path {value!s} is deprecated; use {alias}",
            DeprecationWarning,
            stacklevel=2,
        )
        return resolve_benchmark_path(alias, must_exist=True)
    return resolve_benchmark_path(value, must_exist=True)
