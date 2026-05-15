import psutil
from mesa_memory.config import MesaConfig, calculate_dynamic_limits


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
