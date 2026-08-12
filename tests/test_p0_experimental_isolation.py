from mesa_memory.config import MesaConfig


def test_experimental_features_disabled_by_default():
    """Verify experimental features are isolated and disabled by default."""
    cfg = MesaConfig()

    assert cfg.v4_rebuild_enabled is False
    assert cfg.crossencoder_enabled is False
    assert cfg.rebel_enabled is False


def test_worker_runtime_composition_isolates_cognitive_writers():
    """Verify that worker composition root does not instantiate cognitive writers by default."""
    import inspect
    from mesa_memory import worker_runtime

    source = inspect.getsource(worker_runtime._run_worker_owned)
    # Ensure nonessential background mutation loops (REM, PageRank, entity rewrite, Valence) are NOT started in worker composition root
    assert "ConsolidationLoop" not in source
    assert "PageRank" not in source
    assert "ValenceWorker" not in source
    assert "EntityRewriter" not in source
