"""Explicit offline adoption for legacy vector provider provenance."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from mesa_memory.config import config
from mesa_memory.rebuild_runner import main as rebuild_main
from mesa_storage.embedding_identity import (
    EmbeddingIdentityAdoptionError,
    adopt_legacy_embedding_identity,
)
from mesa_storage.writer_lock import StorageWriterLock

_OPERATION_ID = "11111111-2222-4333-8444-555555555555"


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _legacy_storage(tmp_path: Path, *, model: str = "embed-model") -> tuple[Path, Path]:
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    database = storage / "mesa.db"
    command.upgrade(_config(database), "head")
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO system_operations (operation_id, operation_kind, scope_kind, "
        "scope_key, requested_by_principal_id, idempotency_key, payload_hash, "
        "state) VALUES (?, 'PROJECTION_REBUILD', 'STORAGE_ROOT', 'default', "
        "'admin-a', 'adoption-a', ?, 'PENDING')",
        (_OPERATION_ID, "a" * 64),
    )
    connection.execute(
        "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, "
        "agent_id, state) VALUES ('pipeline-a', 'tenant-a', 'session-a', "
        "'agent-a', 'COMMITTED')"
    )
    connection.execute(
        "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, "
        "agent_id, session_id, content_payload, pipeline_run_id, "
        "embedding_model, embedding_dimension, state) VALUES "
        "('mutation-a', 'candidate-a', 'tenant-a', 'agent-a', 'session-a', "
        "'source', 'pipeline-a', ?, 3, 'COMMITTED')",
        (model,),
    )
    connection.execute(
        "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, "
        "dataset_id, store_name, artifact_kind, physical_artifact_id, state) "
        "VALUES ('registry-a', 'tenant-a', 'agent-a', 'dataset-a', 'VECTOR', "
        "'ENTITY_VECTOR', 'entity-a', 'ACTIVE')"
    )
    connection.execute(
        "INSERT INTO artifact_sources (source_ownership_id, registry_id, "
        "mutation_id, pipeline_run_id, dataset_id, source_ref, state) VALUES "
        "('source-a', 'registry-a', 'mutation-a', 'pipeline-a', 'dataset-a', "
        "'source-a', 'ACTIVE')"
    )
    connection.commit()
    connection.close()
    return trusted, storage


def test_offline_adoption_fills_only_missing_legacy_provider_identity(
    tmp_path: Path,
) -> None:
    trusted, storage = _legacy_storage(tmp_path)

    with StorageWriterLock.acquire(storage, owner="provider-adoption") as writer_lock:
        updated = adopt_legacy_embedding_identity(
            trusted_root=trusted,
            storage_root=storage,
            writer_lock=writer_lock,
            provider="local-test",
            model="embed-model",
            version="v1",
            dimension=3,
        )

    connection = sqlite3.connect(storage / "mesa.db")
    identity = connection.execute(
        "SELECT embedding_provider, embedding_model, embedding_version, "
        "embedding_dimension FROM memory_mutations WHERE mutation_id = 'mutation-a'"
    ).fetchone()
    connection.close()
    assert updated == 1
    assert identity == ("local-test", "embed-model", "v1", 3)


def test_offline_adoption_rejects_conflicting_legacy_signature(tmp_path: Path) -> None:
    trusted, storage = _legacy_storage(tmp_path, model="other-model")

    with StorageWriterLock.acquire(storage, owner="provider-adoption") as writer_lock:
        with pytest.raises(EmbeddingIdentityAdoptionError, match="conflicts"):
            adopt_legacy_embedding_identity(
                trusted_root=trusted,
                storage_root=storage,
                writer_lock=writer_lock,
                provider="local-test",
                model="embed-model",
                version="v1",
                dimension=3,
            )

    connection = sqlite3.connect(storage / "mesa.db")
    provider = connection.execute(
        "SELECT embedding_provider FROM memory_mutations "
        "WHERE mutation_id = 'mutation-a'"
    ).fetchone()[0]
    connection.close()
    assert provider is None


def test_offline_adoption_rejects_checkpointed_retryable_operation(
    tmp_path: Path,
) -> None:
    trusted, storage = _legacy_storage(tmp_path)
    connection = sqlite3.connect(storage / "mesa.db")
    connection.execute(
        "UPDATE system_operations SET state = 'RETRYABLE_FAILED', "
        "source_manifest_hash = ? WHERE operation_id = ?",
        ("b" * 64, _OPERATION_ID),
    )
    connection.commit()
    connection.close()

    with StorageWriterLock.acquire(storage, owner="provider-adoption") as writer_lock:
        with pytest.raises(EmbeddingIdentityAdoptionError, match="source manifest"):
            adopt_legacy_embedding_identity(
                trusted_root=trusted,
                storage_root=storage,
                writer_lock=writer_lock,
                provider="local-test",
                model="embed-model",
                version="v1",
                dimension=3,
            )

    connection = sqlite3.connect(storage / "mesa.db")
    provider = connection.execute(
        "SELECT embedding_provider FROM memory_mutations "
        "WHERE mutation_id = 'mutation-a'"
    ).fetchone()[0]
    connection.close()
    assert provider is None


def test_offline_adoption_rejects_hashless_retryable_operation(tmp_path: Path) -> None:
    trusted, storage = _legacy_storage(tmp_path)
    connection = sqlite3.connect(storage / "mesa.db")
    connection.execute(
        "UPDATE system_operations SET state = 'RETRYABLE_FAILED', "
        "attempt_count = 1 WHERE operation_id = ?",
        (_OPERATION_ID,),
    )
    connection.commit()
    connection.close()

    with StorageWriterLock.acquire(storage, owner="provider-adoption") as writer_lock:
        with pytest.raises(EmbeddingIdentityAdoptionError, match="fresh pending"):
            adopt_legacy_embedding_identity(
                trusted_root=trusted,
                storage_root=storage,
                writer_lock=writer_lock,
                provider="local-test",
                model="embed-model",
                version="v1",
                dimension=3,
            )

    connection = sqlite3.connect(storage / "mesa.db")
    provider = connection.execute(
        "SELECT embedding_provider FROM memory_mutations "
        "WHERE mutation_id = 'mutation-a'"
    ).fetchone()[0]
    connection.close()
    assert provider is None


def test_rebuild_cli_exposes_explicit_provider_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trusted, storage = _legacy_storage(tmp_path)
    monkeypatch.setattr(config, "v4_rebuild_enabled", True)

    exit_code = rebuild_main(
        [
            "adopt-provider",
            "--trusted-root",
            str(trusted),
            "--storage-root",
            str(storage),
            "--provider",
            "local-test",
            "--model",
            "embed-model",
            "--version",
            "v1",
            "--dimension",
            "3",
            "--confirm-legacy-provider-unknown",
        ]
    )

    assert exit_code == 0
    assert '"status": "adopted"' in capsys.readouterr().out
