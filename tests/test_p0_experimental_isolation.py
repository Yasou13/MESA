from mesa_memory.config import MesaConfig


def test_experimental_features_disabled_by_default():
    """Verify experimental features are isolated and disabled by default."""
    cfg = MesaConfig()

    assert cfg.v4_rebuild_enabled is False
    assert cfg.crossencoder_enabled is False
    assert cfg.rebel_enabled is False
