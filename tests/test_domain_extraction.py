"""Extraction configuration defaults."""

import os
from unittest.mock import patch


class TestConfigDefaults:
    """Verify REBEL is disabled and Turkish is the default extraction lang."""

    def test_rebel_disabled_by_default(self):
        # Clear specific env vars that might leak and alter defaults
        with patch.dict(os.environ, {}, clear=True):
            # Re-import or re-instantiate config to ensure it reads the clean environment
            from mesa_memory.config import MesaConfig

            clean_config = MesaConfig()
            assert clean_config.rebel_enabled is False, (
                "MESA_REBEL_ENABLED must default to False — "
                "LLM zero-shot is the primary extraction path"
            )

    def test_extraction_lang_defaults_to_turkish(self):
        from mesa_memory.config import config

        assert config.extraction_lang == "tr", (
            "MESA_EXTRACTION_LANG must default to 'tr' (Turkish)"
        )
