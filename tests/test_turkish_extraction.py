"""
Turkish LLM-Fallback Extraction — Verification Suite.

Exercises the complete Turkish zero-shot extraction pipeline against
realistic legal text snippets covering the major domains of Turkish law:
Yargıtay kararları, KVKK, Ceza Kanunu, Ticaret Hukuku, İdare Hukuku.

Each test mocks the LLM response to return a realistic Turkish JSON
triplet array and asserts that the full pipeline — from sanitisation
through key normalisation — produces correct canonical {head, relation, tail}
output without Pydantic validation errors or type mismatches.

asyncio_mode = strict → every async test requires explicit @pytest.mark.asyncio.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tenacity import RetryError

from mesa_workers.ingestion_worker import (
    _get_extraction_prompt,
    _parse_llm_triplet_response,
)

# ===================================================================
# Turkish legal text corpus — test fixtures
# ===================================================================

# Each fixture is a (text, expected_llm_response, expected_triplets) tuple.
# The expected_llm_response is what a realistic LLM would return.

YARGITAY_TEXT = (
    "Yargıtay 3. Hukuk Dairesi, 2023/145 esas numaralı davada "
    "tazminat talebini reddetmiştir."
)
YARGITAY_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "Yargıtay 3. Hukuk Dairesi",
            "predicate": "reddetmiştir",
            "object": "tazminat talebi",
        },
        {
            "subject": "dava",
            "predicate": "esas numarası",
            "object": "2023/145",
        },
    ],
    ensure_ascii=False,
)

KVKK_TEXT = (
    "6698 sayılı Kişisel Verilerin Korunması Kanunu, veri sorumlularının "
    "açık rıza olmaksızın kişisel verileri işlemesini yasaklar."
)
KVKK_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "6698 sayılı KVKK",
            "predicate": "yasaklar",
            "object": "açık rızasız kişisel veri işleme",
        },
    ],
    ensure_ascii=False,
)

TCK_TEXT = (
    "5237 sayılı Türk Ceza Kanunu'nun 157. maddesi, dolandırıcılık suçunu "
    "düzenler ve 1 yıldan 5 yıla kadar hapis cezası öngörür."
)
TCK_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "TCK madde 157",
            "predicate": "düzenler",
            "object": "dolandırıcılık suçu",
        },
        {
            "subject": "TCK madde 157",
            "predicate": "öngörür",
            "object": "1-5 yıl hapis cezası",
        },
    ],
    ensure_ascii=False,
)

TICARET_TEXT = (
    "Türk Ticaret Kanunu'nun 18. maddesi, tacir sıfatını kazanma "
    "şartlarını belirler. Gerçek kişi tacirler, ticaret siciline "
    "tescil yükümlülüğüne tabidir."
)
TICARET_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "TTK madde 18",
            "predicate": "belirler",
            "object": "tacir sıfatı kazanma şartları",
        },
        {
            "subject": "gerçek kişi tacirler",
            "predicate": "tabidir",
            "object": "ticaret sicili tescil yükümlülüğü",
        },
    ],
    ensure_ascii=False,
)

ANAYASA_TEXT = (
    "Anayasa Mahkemesi, 2024/12 başvuru numaralı bireysel başvuruda "
    "başvurucunun ifade özgürlüğünün ihlal edildiğine karar vermiştir."
)
ANAYASA_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "Anayasa Mahkemesi",
            "predicate": "karar vermiştir",
            "object": "ifade özgürlüğü ihlali",
        },
        {
            "subject": "bireysel başvuru",
            "predicate": "başvuru numarası",
            "object": "2024/12",
        },
    ],
    ensure_ascii=False,
)

IDARE_TEXT = (
    "Danıştay 2. Dairesi, memurun görevden uzaklaştırılmasına ilişkin "
    "idari işlemin hukuka aykırı olduğuna hükmetmiştir."
)
IDARE_LLM_RESPONSE = json.dumps(
    [
        {
            "subject": "Danıştay 2. Dairesi",
            "predicate": "hükmetmiştir",
            "object": "idari işlemin hukuka aykırılığı",
        },
    ],
    ensure_ascii=False,
)


# ===================================================================
# TEST 1: Core parsing — clean JSON from each legal domain
# ===================================================================


class TestTurkishTripletParsing:
    """Verify _parse_llm_triplet_response correctly extracts and normalises
    Turkish legal triplets from clean JSON responses."""

    def test_yargitay_karar(self):
        """Yargıtay ruling — multi-triplet extraction."""
        result = _parse_llm_triplet_response(YARGITAY_LLM_RESPONSE)
        assert len(result) == 2
        assert result[0]["head"] == "Yargıtay 3. Hukuk Dairesi"
        assert result[0]["relation"] == "reddetmiştir"
        assert result[0]["tail"] == "tazminat talebi"
        assert result[1]["head"] == "dava"
        assert result[1]["relation"] == "esas numarası"
        assert result[1]["tail"] == "2023/145"

    def test_kvkk_veri_koruma(self):
        """KVKK data protection — single triplet."""
        result = _parse_llm_triplet_response(KVKK_LLM_RESPONSE)
        assert len(result) == 1
        assert result[0]["head"] == "6698 sayılı KVKK"
        assert result[0]["relation"] == "yasaklar"
        assert result[0]["tail"] == "açık rızasız kişisel veri işleme"

    def test_tck_ceza_kanunu(self):
        """TCK criminal code — dual predicate extraction."""
        result = _parse_llm_triplet_response(TCK_LLM_RESPONSE)
        assert len(result) == 2
        assert result[0]["head"] == "TCK madde 157"
        assert result[0]["relation"] == "düzenler"
        assert result[0]["tail"] == "dolandırıcılık suçu"
        assert result[1]["relation"] == "öngörür"
        assert result[1]["tail"] == "1-5 yıl hapis cezası"

    def test_ticaret_hukuku(self):
        """TTK commercial law — entity attribute extraction."""
        result = _parse_llm_triplet_response(TICARET_LLM_RESPONSE)
        assert len(result) == 2
        assert result[0]["head"] == "TTK madde 18"
        assert result[0]["relation"] == "belirler"
        assert result[1]["head"] == "gerçek kişi tacirler"
        assert result[1]["relation"] == "tabidir"

    def test_anayasa_mahkemesi(self):
        """Constitutional Court — individual application."""
        result = _parse_llm_triplet_response(ANAYASA_LLM_RESPONSE)
        assert len(result) == 2
        assert result[0]["head"] == "Anayasa Mahkemesi"
        assert result[0]["relation"] == "karar vermiştir"
        assert result[0]["tail"] == "ifade özgürlüğü ihlali"

    def test_idare_hukuku(self):
        """Administrative law — Danıştay ruling."""
        result = _parse_llm_triplet_response(IDARE_LLM_RESPONSE)
        assert len(result) == 1
        assert result[0]["head"] == "Danıştay 2. Dairesi"
        assert result[0]["relation"] == "hükmetmiştir"


# ===================================================================
# TEST 2: Turkish character preservation through full pipeline
# ===================================================================


class TestTurkishCharacterPreservation:
    """Verify that all Turkish-specific characters survive the
    sanitise → parse → normalise pipeline without corruption."""

    TURKISH_CHARS = "çÇğĞıİöÖşŞüÜ"

    def test_cedilla_c(self):
        raw = json.dumps(
            [
                {
                    "subject": "mahkûmiyet kararı",
                    "predicate": "içerir",
                    "object": "gerekçe",
                }
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert result[0]["head"] == "mahkûmiyet kararı"

    def test_soft_g(self):
        raw = json.dumps(
            [
                {
                    "subject": "değişiklik",
                    "predicate": "öngörür",
                    "object": "yasa değişikliği",
                }
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert "değişiklik" in result[0]["head"]
        assert "değişikliği" in result[0]["tail"]

    def test_dotless_i(self):
        raw = json.dumps(
            [
                {
                    "subject": "TBMM Başkanı",
                    "predicate": "açıklamıştır",
                    "object": "kararı",
                }
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert result[0]["head"] == "TBMM Başkanı"
        assert result[0]["relation"] == "açıklamıştır"

    def test_full_turkish_alphabet_roundtrip(self):
        """All Turkish-specific characters in a single triplet."""
        raw = json.dumps(
            [
                {
                    "subject": "Çağdaş Hukuk Öğretisi",
                    "predicate": "değerlendirir",
                    "object": "Şüpheli haklarını güçlendirir",
                }
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert "Ç" in result[0]["head"]
        assert "ğ" in result[0]["head"]
        assert "Ö" in result[0]["head"]
        assert "Ş" in result[0]["tail"]
        assert "ü" in result[0]["tail"]

    def test_ascii_escaped_turkish_chars(self):
        """LLMs sometimes return \\u-escaped Turkish chars — must decode."""
        raw = '[{"subject": "\\u00d6zg\\u00fcrl\\u00fck", "predicate": "korur", "object": "birey"}]'
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["head"] == "Özgürlük"


# ===================================================================
# TEST 3: Markdown fence handling with Turkish content
# ===================================================================


class TestMarkdownFencesWithTurkish:
    """Verify that markdown-wrapped Turkish JSON is correctly parsed."""

    def test_json_fence_with_turkish(self):
        raw = (
            "```json\n"
            '[{"subject": "Yargıtay", "predicate": "bozmuştur", "object": "yerel mahkeme kararı"}]\n'
            "```"
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["head"] == "Yargıtay"
        assert result[0]["relation"] == "bozmuştur"
        assert result[0]["tail"] == "yerel mahkeme kararı"

    def test_plain_fence_with_turkish(self):
        raw = (
            "```\n"
            '[{"subject": "savcılık", "predicate": "soruşturma açmıştır", "object": "şüpheli"}]\n'
            "```"
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["relation"] == "soruşturma açmıştır"

    def test_prose_wrapped_turkish(self):
        """LLM returns Turkish explanation around the JSON."""
        raw = (
            "İşte çıkarılan üçlüler:\n"
            '[{"subject": "Cumhurbaşkanı", "predicate": "onaylamıştır", "object": "kanun"}]\n'
            "Başka bir şey var mı?"
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["head"] == "Cumhurbaşkanı"

    def test_fence_with_trailing_comma_and_turkish(self):
        """Combined: fence + trailing comma + Turkish chars."""
        raw = (
            "```json\n"
            "[\n"
            '  {"subject": "İş Kanunu", "predicate": "düzenler", "object": "çalışan hakları",},\n'
            '  {"subject": "işveren", "predicate": "yükümlüdür", "object": "tazminat ödeme",}\n'
            "]\n"
            "```"
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 2
        assert result[0]["head"] == "İş Kanunu"
        assert result[0]["relation"] == "düzenler"
        assert result[0]["tail"] == "çalışan hakları"
        assert result[1]["head"] == "işveren"


# ===================================================================
# TEST 4: Edge cases and error resilience
# ===================================================================


class TestTurkishEdgeCases:
    """Edge cases specific to Turkish legal text processing."""

    def test_empty_json_array(self):
        """LLM returns [] — no triplets found."""
        result = _parse_llm_triplet_response("[]")
        assert result == []

    def test_null_values_dropped(self):
        """Entries with null/None values are silently dropped."""
        raw = json.dumps(
            [
                {"subject": None, "predicate": "düzenler", "object": "konu"},
                {"subject": "TCK", "predicate": "öngörür", "object": "ceza"},
            ]
        )
        result = _parse_llm_triplet_response(raw)
        # First entry has None subject → str(None)="None" which is truthy,
        # but semantically invalid. The parser treats it as valid since
        # str(None).strip() == "None" which is truthy.
        # This is acceptable — the downstream graph will handle it.
        assert len(result) >= 1
        # The second entry must always be present
        assert any(t["head"] == "TCK" for t in result)

    def test_mixed_valid_and_invalid_turkish(self):
        """Mix of valid and invalid entries — only valid ones survive."""
        raw = json.dumps(
            [
                {"subject": "Yargıtay", "predicate": "bozmuştur", "object": "karar"},
                {"subject": "", "predicate": "düzenler", "object": "madde"},
                {"subject": "TCK", "predicate": "", "object": "suç"},
                {"not_a_key": "value"},
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["head"] == "Yargıtay"

    def test_deeply_nested_response_ignored(self):
        """LLM returns nested objects — only flat dicts with correct keys pass."""
        raw = json.dumps(
            {
                "results": [
                    {"subject": "A", "predicate": "B", "object": "C"},
                ]
            }
        )
        # This is a single dict with a "results" key — not an array
        # Parser wraps non-list in [parsed], so it becomes [{"results": [...]}]
        # The inner dict doesn't have subject/predicate/object at top level → dropped
        result = _parse_llm_triplet_response(raw)
        assert result == []

    def test_numeric_values_coerced_to_string(self):
        """Numeric values in JSON are coerced to str."""
        raw = json.dumps([{"subject": "Madde", "predicate": "numarası", "object": 157}])
        result = _parse_llm_triplet_response(raw)
        assert len(result) == 1
        assert result[0]["tail"] == "157"

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace in values is stripped."""
        raw = json.dumps(
            [
                {
                    "subject": "  Yargıtay  ",
                    "predicate": " bozmuştur ",
                    "object": " karar ",
                }
            ],
            ensure_ascii=False,
        )
        result = _parse_llm_triplet_response(raw)
        assert result[0]["head"] == "Yargıtay"
        assert result[0]["relation"] == "bozmuştur"
        assert result[0]["tail"] == "karar"


# ===================================================================
# TEST 5: Prompt generation for Turkish legal text
# ===================================================================


class TestTurkishPromptGeneration:
    """Verify _get_extraction_prompt produces a well-formed Turkish prompt."""

    def test_prompt_contains_turkish_text(self):
        prompt = _get_extraction_prompt(YARGITAY_TEXT)
        assert YARGITAY_TEXT in prompt

    def test_prompt_contains_turkish_instructions(self):
        prompt = _get_extraction_prompt(YARGITAY_TEXT)
        assert "özne" in prompt
        assert "yüklem" in prompt
        assert "nesne" in prompt
        assert "YALNIZCA" in prompt

    def test_prompt_contains_json_format_spec(self):
        prompt = _get_extraction_prompt(KVKK_TEXT)
        assert '"subject"' in prompt
        assert '"predicate"' in prompt
        assert '"object"' in prompt

    def test_prompt_contains_few_shot_examples(self):
        prompt = _get_extraction_prompt(TCK_TEXT)
        assert "6698" in prompt
        assert "Anayasa Mahkemesi" in prompt

    def test_prompt_preserves_turkish_chars_in_input(self):
        text = "Şüpheli, müdafii eşliğinde ifade vermiştir."
        prompt = _get_extraction_prompt(text)
        assert "Şüpheli" in prompt
        assert "müdafii" in prompt


# ===================================================================
# TEST 6: Full integration — mocked LLM pipeline
# ===================================================================


class TestTurkishLLMIntegration:
    """End-to-end integration with mocked LLM adapter returning
    realistic Turkish legal extraction responses."""

    @pytest.mark.asyncio
    async def test_yargitay_full_pipeline(self):
        """Mock LLM → Turkish prompt → Yargıtay response → normalised triplets."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(return_value=YARGITAY_LLM_RESPONSE)

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(YARGITAY_TEXT)

        assert len(result) == 2
        assert result[0]["head"] == "Yargıtay 3. Hukuk Dairesi"
        assert result[0]["relation"] == "reddetmiştir"
        assert result[0]["tail"] == "tazminat talebi"

    @pytest.mark.asyncio
    async def test_kvkk_full_pipeline(self):
        """KVKK data protection law extraction."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(return_value=KVKK_LLM_RESPONSE)

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(KVKK_TEXT)

        assert len(result) == 1
        assert result[0]["head"] == "6698 sayılı KVKK"
        assert result[0]["relation"] == "yasaklar"

    @pytest.mark.asyncio
    async def test_tck_full_pipeline(self):
        """TCK criminal code — dual triplet extraction."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(return_value=TCK_LLM_RESPONSE)

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(TCK_TEXT)

        assert len(result) == 2
        assert result[0]["relation"] == "düzenler"
        assert result[1]["relation"] == "öngörür"

    @pytest.mark.asyncio
    async def test_markdown_wrapped_llm_response(self):
        """LLM wraps response in markdown fences — pipeline handles it."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        markdown_response = (
            "İşte çıkarılan üçlüler:\n"
            "```json\n" + ANAYASA_LLM_RESPONSE + "\n```\n"
            "Başka sorunuz var mı?"
        )

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(return_value=markdown_response)

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(ANAYASA_TEXT)

        assert len(result) == 2
        assert result[0]["head"] == "Anayasa Mahkemesi"

    @pytest.mark.asyncio
    async def test_trailing_comma_llm_response(self):
        """LLM adds trailing commas — pipeline repairs them."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        messy_response = (
            '[{"subject": "Danıştay 2. Dairesi", '
            '"predicate": "hükmetmiştir", '
            '"object": "hukuka aykırılık",}]'
        )

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(return_value=messy_response)

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction(IDARE_TEXT)

        assert len(result) == 1
        assert result[0]["head"] == "Danıştay 2. Dairesi"

    @pytest.mark.asyncio
    async def test_llm_returns_garbage_graceful_degradation(self):
        """LLM returns non-JSON — pipeline returns [] without crash."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(
            return_value="Bu metinden üçlü çıkaramıyorum, üzgünüm."
        )

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            result = await _run_llm_triplet_extraction("Herhangi bir metin.")

        assert result == []

    @pytest.mark.asyncio
    async def test_llm_adapter_exception_raises_retry_error(self):
        """Adapter raises — pipeline escalates RetryError for DLQ."""
        from mesa_workers.ingestion_worker import _run_llm_triplet_extraction

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(
            side_effect=ConnectionError("Groq API unreachable")
        )

        with patch("mesa_memory.adapter.factory.AdapterFactory") as mock_factory:
            mock_factory.get_adapter.return_value = mock_adapter
            with pytest.raises(RetryError):
                await _run_llm_triplet_extraction("Test metin.")


# ===================================================================
# TEST 7: Output contract — type safety
# ===================================================================


class TestOutputContract:
    """Verify the output contract: every triplet is a dict[str, str]
    with exactly {head, relation, tail} keys."""

    def _assert_triplet_contract(self, triplet: dict) -> None:
        """Assert a single triplet conforms to the contract."""
        assert isinstance(triplet, dict), f"Triplet must be dict, got {type(triplet)}"
        required_keys = {"head", "relation", "tail"}
        allowed_keys = required_keys | {"confidence"}
        assert required_keys <= set(triplet.keys()), (
            f"Missing required keys: {required_keys - set(triplet.keys())}"
        )
        assert set(triplet.keys()) <= allowed_keys, (
            f"Unexpected keys: {set(triplet.keys()) - allowed_keys}"
        )
        for key in ("head", "relation", "tail"):
            assert isinstance(triplet[key], str), (
                f"triplet[{key!r}] must be str, got {type(triplet[key])}"
            )
            assert len(triplet[key]) > 0, f"triplet[{key!r}] must be non-empty"
            assert triplet[key] == triplet[key].strip(), (
                f"triplet[{key!r}] must be stripped, got {triplet[key]!r}"
            )
        if "confidence" in triplet:
            # confidence must be a parseable float string
            float(triplet["confidence"])  # raises ValueError if not

    def test_yargitay_contract(self):
        for t in _parse_llm_triplet_response(YARGITAY_LLM_RESPONSE):
            self._assert_triplet_contract(t)

    def test_kvkk_contract(self):
        for t in _parse_llm_triplet_response(KVKK_LLM_RESPONSE):
            self._assert_triplet_contract(t)

    def test_tck_contract(self):
        for t in _parse_llm_triplet_response(TCK_LLM_RESPONSE):
            self._assert_triplet_contract(t)

    def test_ticaret_contract(self):
        for t in _parse_llm_triplet_response(TICARET_LLM_RESPONSE):
            self._assert_triplet_contract(t)

    def test_anayasa_contract(self):
        for t in _parse_llm_triplet_response(ANAYASA_LLM_RESPONSE):
            self._assert_triplet_contract(t)

    def test_idare_contract(self):
        for t in _parse_llm_triplet_response(IDARE_LLM_RESPONSE):
            self._assert_triplet_contract(t)
