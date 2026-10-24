"""Extraction configuration defaults."""


class TestConfigDefaults:
    """Verify REBEL is disabled and Turkish is the default extraction lang."""


    def test_extraction_lang_defaults_to_turkish(self):
        from mesa_memory.config import config

        assert config.extraction_lang == "tr", (
            "MESA_EXTRACTION_LANG must default to 'tr' (Turkish)"
        )
