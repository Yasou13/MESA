from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from mesa_memory.ports import IngestionQueue as IngestionQueuePort
from mesa_memory.ports import MutationLedger as MutationLedgerPort
from mesa_memory.ports import ProjectionStore as ProjectionStorePort
from mesa_runtime.app import RuntimeContainer, create_app
from mesa_storage.dao import MemoryDAO
from mesa_storage.modules import IngestionQueue, MutationLedger, ProjectionStore


def test_memory_dao_facade_contains_no_sql_or_backend_access() -> None:
    source = Path(inspect.getsourcefile(MemoryDAO) or "").read_text(encoding="utf-8")

    assert "SELECT " not in source
    assert "INSERT " not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source
    assert "aiosqlite" not in source
    assert "VectorEngine" not in source
    assert "KuzuGraphProvider" not in source


def test_capability_modules_expose_only_their_owned_operations() -> None:
    kernel = SimpleNamespace(
        admit_v4_memory=lambda: None,
        claim_projection_outbox=lambda: None,
        claim_raw_log=lambda: None,
        purge_memory=lambda: None,
    )

    mutation = MutationLedger(kernel)  # type: ignore[arg-type]
    projection = ProjectionStore(kernel)  # type: ignore[arg-type]
    queue = IngestionQueue(kernel)  # type: ignore[arg-type]

    assert mutation.admit_v4_memory is kernel.admit_v4_memory
    assert projection.claim_projection_outbox is kernel.claim_projection_outbox
    assert queue.claim_raw_log is kernel.claim_raw_log
    with pytest.raises(AttributeError):
        _ = mutation.purge_memory
    with pytest.raises(AttributeError):
        _ = projection.admit_v4_memory


def test_memory_dao_satisfies_transitional_capability_ports() -> None:
    required = {
        MutationLedgerPort: (
            "admit_v4_memory",
            "get_mutation_summary",
            "set_mutation_state",
            "transition_pipeline_run",
        ),
        ProjectionStorePort: (
            "get_projection_mutation",
            "claim_projection_outbox",
            "project_v4_sql_entity",
        ),
        IngestionQueuePort: (
            "admit_raw_log",
            "claim_raw_log",
            "request_session_finalization",
            "claim_dispatch_queue",
        ),
    }

    for port, methods in required.items():
        assert all(hasattr(MemoryDAO, method) for method in methods), port


def test_app_factory_owns_an_independent_runtime_container() -> None:
    first = create_app()
    second = create_app()

    assert isinstance(first.state.container, RuntimeContainer)
    assert isinstance(second.state.container, RuntimeContainer)
    assert first.state.container is not second.state.container
