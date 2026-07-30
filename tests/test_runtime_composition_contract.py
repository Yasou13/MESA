"""Contracts for the canonical runtime composition root."""

from __future__ import annotations

import importlib

from mesa_memory.config import RuntimeProfile, RuntimeProfileConfig
from mesa_runtime.app import create_app
from scripts.check_layer_imports import (
    find_package_cycles,
    find_reverse_dependencies,
)


def _route_signatures(application) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (route.path, tuple(sorted(getattr(route, "methods", ()))))
        for route in application.routes
        if hasattr(route, "path")
    }


def test_app_factory_preserves_the_canonical_route_contract(tmp_path) -> None:
    settings = RuntimeProfileConfig(
        profile=RuntimeProfile.API_ONLY,
        storage_root=tmp_path / "storage",
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=False,
        require_worker_readiness=False,
    )

    canonical = importlib.import_module("mesa_runtime.app")
    created = create_app(settings)

    assert created is not canonical.app
    assert created.state.runtime_settings == settings
    assert _route_signatures(created) == _route_signatures(canonical.app)


def test_legacy_server_import_aliases_the_canonical_module() -> None:
    canonical = importlib.import_module("mesa_runtime.app")
    legacy = importlib.import_module("mesa_memory.api.server")

    assert legacy is canonical
    assert legacy.app is canonical.app


def test_production_package_graph_has_no_reverse_edges_or_cycles() -> None:
    assert find_reverse_dependencies() == []
    assert find_package_cycles() == []
