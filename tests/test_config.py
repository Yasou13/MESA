import psutil

from mesa_memory.config import (
    MesaConfig,
    calculate_dynamic_limits,
    config,
    configured_embedding_identity,
)


def test_env_variable_override(monkeypatch):
    # Pydantic BaseSettings maps field 'context_window_limit' to env var
    # CONTEXT_WINDOW_LIMIT (no prefix). Suppress .env file so the env-var
    # override set here is the sole source of truth.
    monkeypatch.setenv("CONTEXT_WINDOW_LIMIT", "9000")
    cfg = MesaConfig(_env_file=None)
    assert cfg.context_window_limit == 9000


def test_dynamic_ram_limit():
    cfg = calculate_dynamic_limits(MesaConfig())
    assert isinstance(cfg.lancedb_memory_limit_bytes, int)
    assert cfg.lancedb_memory_limit_bytes == int(psutil.virtual_memory().total * 0.18)
    assert 1 <= cfg.vector_worker_limit <= 4


def test_v4_rebuild_feature_flag_is_disabled_by_default_and_explicitly_enabled(
    monkeypatch,
):
    monkeypatch.delenv("MESA_V4_REBUILD_ENABLED", raising=False)
    assert MesaConfig(_env_file=None).v4_rebuild_enabled is False

    monkeypatch.setenv("MESA_V4_REBUILD_ENABLED", "true")
    assert MesaConfig(_env_file=None).v4_rebuild_enabled is True


def test_embedding_identity_has_a_nonempty_version_and_tracks_provider_mode():
    identity = configured_embedding_identity(
        {"MESA_EXTERNAL_PROVIDER_ENABLED": "false"}
    )

    assert identity.provider == "local"
    assert identity.model == config.local_embedding_model
    assert identity.version == "v1"
    assert identity.dimension > 0
    assert identity.normalized is True

    external_identity = configured_embedding_identity(
        {"MESA_EXTERNAL_PROVIDER_ENABLED": "true"}
    )
    assert external_identity.provider == config.embedding_provider
    assert external_identity.model == config.external_embedding_model
