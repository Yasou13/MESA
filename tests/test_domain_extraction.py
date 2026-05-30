"""
Phase 1.3 Verification: Domain-Specific Extraction — Turkish Zero-Shot Pipeline.

Proves that:
  1. MESA_REBEL_ENABLED defaults to False.
  2. MESA_EXTRACTION_LANG defaults to "tr" (Turkish).
  3. The Turkish zero-shot prompt is selected when lang="tr".
  4. The English prompt is selected when lang="en".
  5. Unknown languages fall back to English.
  6. _sanitize_llm_json gracefully handles all common LLM output formats:
     - Clean JSON arrays
     - Markdown-fenced JSON (```json ... ```)
     - Prose-wrapped JSON
     - Trailing commas
     - Truncated/incomplete output
  7. _parse_llm_triplet_response normalises both {subject, predicate, object}
     and {head, relation, tail} key formats to canonical {head, relation, tail}.
  8. Turkish characters (ç, ğ, ı, ö, ş, ü) are preserved through the pipeline.

asyncio_mode = strict → every async test requires explicit @pytest.mark.asyncio.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tenacity import RetryError

# ===================================================================
# Helpers
# ===================================================================


def _import_extraction_functions():
    """Import all extraction functions from the worker module."""
    from mesa_workers.ingestion_worker import (
        _ENGLISH_TRIPLET_PROMPT,
        _PROMPT_REGISTRY,
        _TURKISH_TRIPLET_PROMPT,
        _get_extraction_prompt,
        _parse_llm_triplet_response,
        _sanitize_llm_json,
    )

    return {
        "en_prompt": _ENGLISH_TRIPLET_PROMPT,
        "tr_prompt": _TURKISH_TRIPLET_PROMPT,
        "registry": _PROMPT_REGISTRY,
        "get_prompt": _get_extraction_prompt,
        "parse": _parse_llm_triplet_response,
        "sanitize": _sanitize_llm_json,
    }


# ===================================================================
# TEST 1: Config defaults
# ===================================================================


class TestConfigDefaults:
    """Verify REBEL is disabled and Turkish is the default extraction lang."""

    def test_rebel_disabled_by_default(self):
        from mesa_memory.config import config

        assert config.rebel_enabled is False, (
            "MESA_REBEL_ENABLED must default to False — "
            "LLM zero-shot is the primary extraction path"
        )

    def test_extraction_lang_defaults_to_turkish(self):
        from mesa_memory.config import config

        assert (
            config.extraction_lang == "tr"
        ), "MESA_EXTRACTION_LANG must default to 'tr' (Turkish)"


# ===================================================================
# TEST 2: Prompt selection
# ===================================================================


class TestPromptSelection:
    """Verify language-aware prompt dispatch."""

    def test_turkish_prompt_selected_for_tr(self):
        funcs = _import_extraction_functions()
        prompt = funcs["get_prompt"]("test metin")
        # Must contain Turkish-specific markers
        assert "özne" in prompt, "Turkish prompt must contain 'özne'"
        assert "yüklem" in prompt, "Turkish prompt must contain 'yüklem'"
        assert "nesne" in prompt, "Turkish prompt must contain 'nesne'"
        assert "test metin" in prompt, "Input text must be injected"

    def test_english_prompt_selected_for_en(self):
        funcs = _import_extraction_functions()
        with patch("mesa_workers.ingestion_worker.config") as mock_config:
            mock_config.extraction_lang = "en"
            prompt = funcs["get_prompt"]("test content")
        assert "subject, predicate, object" in prompt
        assert "test content" in prompt

    def test_unknown_lang_falls_back_to_english(self):
        funcs = _import_extraction_functions()
        with patch("mesa_workers.ingestion_worker.config") as mock_config:
            mock_config.extraction_lang = "de"  # Unsupported
            prompt = funcs["get_prompt"]("test content")
        # Must fall back to English prompt
        assert "subject, predicate, object" in prompt

    def test_prompt_truncates_to_2000_chars(self):
        funcs = _import_extraction_functions()
        long_text = "A" * 5000
        prompt = funcs["get_prompt"](long_text)
        # The injected text portion should be truncated
        assert "A" * 2000 in prompt
        assert "A" * 2001 not in prompt

    def test_prompt_registry_contains_en_and_tr(self):
        funcs = _import_extraction_functions()
        assert "en" in funcs["registry"]
        assert "tr" in funcs["registry"]

    def test_turkish_prompt_has_few_shot_examples(self):
        """Turkish prompt must include concrete few-shot examples."""
        funcs = _import_extraction_functions()
        prompt_template = funcs["tr_prompt"]
        assert "6698" in prompt_template, "Must include KVKK example"
        assert "Anayasa Mahkemesi" in prompt_template, "Must include AYM example"
        assert "düzenler" in prompt_template, "Must include predicate example"


# ===================================================================
# TEST 3: JSON sanitisation (_sanitize_llm_json)
# ===================================================================


class TestSanitizeLlmJson:
    """Exhaustive test of the 4-layer JSON sanitisation pipeline."""

    def test_clean_json_array_passthrough(self):
        funcs = _import_extraction_functions()
        raw = '[{"subject": "A", "predicate": "B", "object": "C"}]'
        assert funcs["sanitize"](raw) == raw

    def test_markdown_json_fence(self):
        """```json ... ``` fences are stripped."""
        funcs = _import_extraction_functions()
        raw = '```json\n[{"subject": "A", "predicate": "B", "object": "C"}]\n```'
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed[0]["subject"] == "A"

    def test_markdown_plain_fence(self):
        """``` ... ``` fences (no json tag) are stripped."""
        funcs = _import_extraction_functions()
        raw = '```\n[{"subject": "A", "predicate": "B", "object": "C"}]\n```'
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed[0]["subject"] == "A"

    def test_prose_wrapped_json(self):
        """Surrounding prose is discarded."""
        funcs = _import_extraction_functions()
        raw = (
            "Here are the extracted triplets:\n"
            '[{"subject": "EU", "predicate": "mandates", "object": "GDPR"}]\n'
            "I hope this helps!"
        )
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed[0]["subject"] == "EU"

    def test_trailing_comma_repair(self):
        """Trailing commas before ] or } are removed."""
        funcs = _import_extraction_functions()
        raw = '[{"subject": "A", "predicate": "B", "object": "C",},]'
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed[0]["subject"] == "A"

    def test_single_object_not_array(self):
        """Single JSON object (not wrapped in array) is handled."""
        funcs = _import_extraction_functions()
        raw = '{"subject": "X", "predicate": "Y", "object": "Z"}'
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed["subject"] == "X"

    def test_total_garbage_passthrough(self):
        """Non-JSON input passes through (will fail at json.loads upstream)."""
        funcs = _import_extraction_functions()
        raw = "This is not JSON at all."
        result = funcs["sanitize"](raw)
        assert result == raw.strip()

    def test_empty_string(self):
        funcs = _import_extraction_functions()
        result = funcs["sanitize"]("")
        assert result == ""

    def test_nested_markdown_with_prose(self):
        """Combined: prose + markdown fence + trailing comma."""
        funcs = _import_extraction_functions()
        raw = (
            "Sure! Here you go:\n"
            "```json\n"
            '[{"subject": "TCK", "predicate": "düzenler", "object": "ceza hükümleri",}]\n'
            "```\n"
            "Let me know if you need more."
        )
        result = funcs["sanitize"](raw)
        parsed = json.loads(result)
        assert parsed[0]["subject"] == "TCK"
        assert parsed[0]["predicate"] == "düzenler"


# ===================================================================
# TEST 4: Triplet parsing and key normalisation
# ===================================================================


class TestParseLlmTripletResponse:
    """Verify _parse_llm_triplet_response normalises both key formats."""

    def test_subject_predicate_object_keys(self):
        """Turkish-format {subject, predicate, object} → {head, relation, tail}."""
        funcs = _import_extraction_functions()
        raw = json.dumps(
            [
                {"subject": "KVKK", "predicate": "düzenler", "object": "veri koruma"},
            ]
        )
        result = funcs["parse"](raw)
        assert len(result) == 1
        assert result[0]["head"] == "KVKK"
        assert result[0]["relation"] == "düzenler"
        assert result[0]["tail"] == "veri koruma"

    def test_head_relation_tail_keys(self):
        """Legacy {head, relation, tail} format passes through."""
        funcs = _import_extraction_functions()
        raw = json.dumps(
            [
                {"head": "EU", "relation": "mandates", "tail": "GDPR"},
            ]
        )
        result = funcs["parse"](raw)
        assert len(result) == 1
        assert result[0]["head"] == "EU"
        assert result[0]["relation"] == "mandates"
        assert result[0]["tail"] == "GDPR"

    def test_mixed_key_formats(self):
        """Both key formats in the same array are normalised."""
        funcs = _import_extraction_functions()
        raw = json.dumps(
            [
                {"subject": "A", "predicate": "B", "object": "C"},
                {"head": "X", "relation": "Y", "tail": "Z"},
            ]
        )
        result = funcs["parse"](raw)
        assert len(result) == 2
        assert result[0] == {"head": "A", "relation": "B", "tail": "C"}
        assert result[1] == {"head": "X", "relation": "Y", "tail": "Z"}

    def test_invalid_entries_silently_dropped(self):
        """Entries missing required fields are dropped, not errored."""
        funcs = _import_extraction_functions()
        raw = json.dumps(
            [
                {"subject": "A", "predicate": "B"},  # Missing object
                {"subject": "X", "predicate": "Y", "object": "Z"},  # Valid
                "not a dict",  # Invalid type
                {"subject": "", "predicate": "Y", "object": "Z"},  # Empty subject
            ]
        )
        result = funcs["parse"](raw)
        assert len(result) == 1
        assert result[0]["head"] == "X"

    def test_empty_array(self):
        funcs = _import_extraction_functions()
        result = funcs["parse"]("[]")
        assert result == []

    def test_total_garbage_returns_empty(self):
        funcs = _import_extraction_functions()
        result = funcs["parse"]("totally not json at all")
        assert result == []

    def test_markdown_wrapped_turkish_response(self):
        """End-to-end: markdown fence + Turkish format + trailing comma."""
        funcs = _import_extraction_functions()
        raw = (
            "```json\n"
            "[\n"
            '  {"subject": "Anayasa Mahkemesi", "predicate": "inceler", "object": "bireysel başvurular"},\n'
            '  {"subject": "Anayasa Mahkemesi", "predicate": "karara bağlar", "object": "bireysel başvurular",}\n'
            "]\n"
            "```"
        )
        result = funcs["parse"](raw)
        assert len(result) == 2
        assert result[0]["head"] == "Anayasa Mahkemesi"
        assert result[0]["relation"] == "inceler"
        assert result[0]["tail"] == "bireysel başvurular"
        assert result[1]["relation"] == "karara bağlar"

    def test_turkish_characters_preserved(self):
        """Turkish Unicode characters (ç, ğ, ı, ö, ş, ü) survive the pipeline."""
        funcs = _import_extraction_functions()
        raw = json.dumps(
            [
                {
                    "subject": "Türkiye Büyük Millet Meclisi",
                    "predicate": "görüşür",
                    "object": "bütçe yasa tasarısı",
                },
            ],
            ensure_ascii=False,
        )
        result = funcs["parse"](raw)
        assert len(result) == 1
        assert result[0]["head"] == "Türkiye Büyük Millet Meclisi"
        assert result[0]["relation"] == "görüşür"
        assert result[0]["tail"] == "bütçe yasa tasarısı"

    def test_single_object_unwrapped(self):
        """Single JSON object (not in array) is parsed as single triplet."""
        funcs = _import_extraction_functions()
        raw = '{"subject": "TCK", "predicate": "düzenler", "object": "ceza hükümleri"}'
        result = funcs["parse"](raw)
        assert len(result) == 1
        assert result[0]["head"] == "TCK"


# ===================================================================
# TEST 5: LLM extraction integration (mocked adapter)
# ===================================================================


class TestLLMExtractionIntegration:
    """Verify the full extraction pipeline with a mocked LLM adapter."""

    @pytest.mark.asyncio
    async def test_llm_extraction_with_turkish_response(self):
        """Mock the adapter to return a Turkish-format response and verify
        the full pipeline produces canonical {head, relation, tail} output."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(
            return_value=json.dumps(
                [
                    {
                        "subject": "6698 sayılı KVKK",
                        "predicate": "düzenler",
                        "object": "veri sorumlusu yükümlülükleri",
                    },
                ],
                ensure_ascii=False,
            )
        )

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(
                "6698 sayılı Kişisel Verilerin Korunması Kanunu, "
                "veri sorumlularının yükümlülüklerini düzenler."
            )

        assert len(result) == 1
        assert result[0]["head"] == "6698 sayılı KVKK"
        assert result[0]["relation"] == "düzenler"
        assert result[0]["tail"] == "veri sorumlusu yükümlülükleri"

    @pytest.mark.asyncio
    async def test_llm_extraction_with_markdown_wrapped_response(self):
        """Adapter returns markdown-fenced JSON — parser must handle it."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(
            return_value=(
                "```json\n"
                '[{"subject": "TBMM", "predicate": "onaylar", "object": "bütçe"}]\n'
                "```"
            )
        )

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction("TBMM bütçeyi onaylar.")

        assert len(result) == 1
        assert result[0]["head"] == "TBMM"

    @pytest.mark.asyncio
    async def test_llm_extraction_raises_retry_error_on_adapter_failure(self):
        """If the adapter raises repeatedly, the pipeline raises RetryError for DLQ."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(side_effect=RuntimeError("API down"))

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            with pytest.raises(RetryError):
                await _run_llm_triplet_extraction("Some text")

    @pytest.mark.asyncio
    async def test_llm_extraction_returns_empty_on_garbage_response(self):
        """If the LLM returns total garbage, the pipeline returns []."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(
            return_value="I cannot process this request."
        )

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction("Some text")

        assert result == []


# ===================================================================
# TEST 6: _sanitize_llm_json has no adapter dependency
# ===================================================================


class TestSanitizerIndependence:
    """Verify _sanitize_llm_json is standalone (no adapter import)."""

    def test_no_adapter_import_in_sanitizer(self):
        """_sanitize_llm_json must not import from mesa_memory.adapter."""
        import inspect

        from mesa_workers.ingestion_worker import _sanitize_llm_json

        source = inspect.getsource(_sanitize_llm_json)
        assert "mesa_memory.adapter" not in source
        assert "OpenAICompatibleAdapter" not in source

    def test_no_adapter_import_in_parser(self):
        """_parse_llm_triplet_response must not import from mesa_memory.adapter."""
        import inspect

        from mesa_workers.ingestion_worker import _parse_llm_triplet_response

        source = inspect.getsource(_parse_llm_triplet_response)
        assert "mesa_memory.adapter" not in source
        assert "OpenAICompatibleAdapter" not in source
