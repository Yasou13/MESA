import json
from pathlib import Path

import pytest
from mesa_benchmark.core.config import load_config
from mesa_benchmark.core.paths import resolve_benchmark_path, resolve_results_root
from mesa_benchmark.core.suite import check_suite, resolve_suite_path
from mesa_benchmark.datasets.loader import DatasetLoaderError, DatasetManager


def test_packaged_smoke_suite_resolves_by_name() -> None:
    path = resolve_suite_path("smoke")
    assert path.name == "smoke.yaml"
    assert "resources" in path.parts


def test_resource_and_data_uris_are_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "benchmark-data"
    payload = data_root / "external" / "fixture.json"
    payload.parent.mkdir(parents=True)
    payload.write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("MESA_BENCHMARK_DATA_DIR", str(data_root))
    monkeypatch.chdir(tmp_path)

    assert resolve_benchmark_path("data://external/fixture.json") == payload
    resource = resolve_benchmark_path("resource://fixtures/internal/mini_dataset.json")
    assert resource.is_file()


def test_relative_dataset_paths_resolve_from_config_directory(tmp_path: Path) -> None:
    dataset = tmp_path / "payload.json"
    manifest = tmp_path / "manifest.json"
    dataset.write_text("[]\n", encoding="utf-8")
    manifest.write_text("{}\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
suite_name: relative
iterations: 1
seed: 42
dataset:
  name: fixture
  version: v1
  path: payload.json
  manifest_path: manifest.json
client:
  name: dummy
  adapter_class: mesa_benchmark.clients.dummy_client.DummyClientAdapter
evaluation: {metrics: [hit_at_k]}
""".strip() + "\n",
        encoding="utf-8",
    )

    loaded = load_config(config)
    assert Path(loaded.dataset.path) == dataset
    assert Path(loaded.dataset.manifest_path or "") == manifest


def test_legacy_config_alias_matches_packaged_config() -> None:
    with pytest.warns(DeprecationWarning, match="legacy benchmark config path"):
        legacy = load_config("mesa-benchmark/config_smoke_dense.yaml")
    packaged = load_config("resource://configs/internal/smoke_dense.yaml")
    assert legacy == packaged


def test_smoke_suite_reports_full_internal_dataset_checks() -> None:
    checked = check_suite("smoke")
    names = {item["id"] for item in checked["dataset_checks"]}
    assert names == {"contradiction-v3", "holdout-600", "multi-hop-raw-v2"}
    assert all(item["ready"] for item in checked["dataset_checks"])


def test_results_root_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment_root = tmp_path / "environment-results"
    explicit_root = tmp_path / "explicit-results"
    monkeypatch.setenv("MESA_BENCHMARK_RESULTS_DIR", str(environment_root))

    assert resolve_results_root(explicit_root) == explicit_root.resolve()
    assert resolve_results_root(None) == environment_root.resolve()


def test_packaged_sources_index_is_valid_json() -> None:
    path = resolve_benchmark_path("resource://manifests/SOURCES.json")
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def test_dataset_python_package_contains_no_payload_json() -> None:
    package = Path(__file__).parents[1] / "mesa_benchmark" / "datasets"
    assert list(package.glob("*.json")) == []


def test_missing_data_uri_has_actionable_sync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MESA_BENCHMARK_DATA_DIR", str(tmp_path / "missing-data"))
    missing = resolve_benchmark_path("data://external/missing.json")

    with pytest.raises(DatasetLoaderError, match="dataset-sync"):
        DatasetManager(missing).load()


def test_missing_packaged_resource_fails_with_resolved_path() -> None:
    with pytest.raises(ValueError, match="benchmark path not found"):
        resolve_benchmark_path("resource://missing/config.yaml", must_exist=True)
