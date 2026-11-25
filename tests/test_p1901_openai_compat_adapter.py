"""P19-01 tests: single OpenAI-compatible adapter against a local fake server (D102)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import openai
import pytest
from pydantic import ValidationError

from mesa_memory.adapter.base import LLMNotConfiguredError, LLMResponseError
from mesa_memory.adapter.factory import AdapterFactory, reset_legacy_warnings
from mesa_memory.adapter.openai_compat import OpenAICompatibleAdapter
from mesa_memory.config import config
from mesa_memory.extraction.service import FactExtractionService
from mesa_memory.retrieval.adaptive import BoundedQueryPlanner, QueryExpansionOutput
from tests.fake_openai_server import FakeOpenAIServer, chat_completion_payload


def test_fake_server_sync_and_async_chat_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(
        path: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        assert path == "/v1/chat/completions"
        assert headers.get("authorization") == "Bearer local"
        return 200, chat_completion_payload(
            f"echo:{body['model']}:{body['messages'][-1]['content']}",
            model=body["model"],
            prompt_tokens=15,
            completion_tokens=7,
        )

    with FakeOpenAIServer(handler) as server:
        monkeypatch.setattr(config, "external_provider_enabled", False)
        monkeypatch.setattr(config, "llm_model", "qwen3:8b")
        monkeypatch.setattr(config, "llm_base_url", server.base_url)
        monkeypatch.setattr(config, "llm_api_key", None)
        monkeypatch.setattr(config, "llm_timeout_seconds", 5.0)

        adapter = AdapterFactory.get_adapter()
        assert isinstance(adapter, OpenAICompatibleAdapter)
        result = adapter.chat(
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "hello"},
            ],
            max_tokens=128,
            temperature=0.0,
        )
        assert result.text == "echo:qwen3:8b:hello"
        assert result.prompt_tokens == 15
        assert result.completion_tokens == 7
        assert result.total_tokens == 22
        assert result.latency_ms >= 0.0
        assert adapter.complete("ping") == "echo:qwen3:8b:ping"


@pytest.mark.asyncio
async def test_fake_server_planner_json_schema_and_400_fallback() -> None:
    # 1. Server supports response_format=json_schema
    def schema_handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["name"] == "QueryExpansionOutput"
        return 200, chat_completion_payload(
            json.dumps({"queries": ["alternative one", "alternative two"]})
        )

    with FakeOpenAIServer(schema_handler) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="local-llm", timeout_seconds=5.0
        )
        planner = BoundedQueryPlanner(adapter)
        plan = await planner.plan("original query")
        assert plan.status == "planner_expanded"
        assert plan.expansions == ("alternative one", "alternative two")

    # 2. Server rejects response_format=json_schema with 400 -> adapter retries plain
    def fallback_handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if "response_format" in body:
            return 400, {"error": {"message": "response_format json_schema unsupported"}}
        return 200, chat_completion_payload(
            '```json\n{"queries": ["fallback query"]}\n```'
        )

    with FakeOpenAIServer(fallback_handler) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="local-llm", timeout_seconds=5.0
        )
        out = await adapter.acomplete("reformulate", schema=QueryExpansionOutput)
        assert isinstance(out, QueryExpansionOutput)
        assert out.queries == ["fallback query"]
        assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_fake_server_fact_extraction_service_works() -> None:
    source = "The company headquarters moved to Ankara on 2024-05-01."

    def handler(
        _path: str, _headers: dict[str, str], _body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        payload = {
            "facts": [
                {
                    "fact_text": "Company headquarters moved to Ankara.",
                    "subject": "company headquarters",
                    "predicate": "moved_to",
                    "object": "Ankara",
                    "object_type": "ENTITY",
                    "valid_from": "2024-05-01",
                    "valid_to": None,
                    "confidence": 0.95,
                    "source_span": "headquarters moved to Ankara",
                    "supersedes": None,
                }
            ]
        }
        return 200, chat_completion_payload(json.dumps(payload))

    with FakeOpenAIServer(handler) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="extract-model", timeout_seconds=5.0
        )
        service = FactExtractionService(llm=adapter, extraction_lang="en")
        facts = await service.extract_facts(source)
        assert len(facts) == 1
        assert facts[0].subject == "company headquarters"
        assert facts[0].object == "Ankara"


def test_fake_server_timeout_raises_promptly() -> None:
    def slow_handler(
        _path: str, _headers: dict[str, str], _body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        time.sleep(0.5)
        return 200, chat_completion_payload("too late")

    with FakeOpenAIServer(slow_handler) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="slow-model", timeout_seconds=0.15
        )
        started = time.perf_counter()
        with pytest.raises(openai.APITimeoutError):
            adapter.complete("hello")
        elapsed = time.perf_counter() - started
        assert elapsed < 1.5
        assert len(server.requests) == 1


def test_fake_server_401_raises_authentication_error() -> None:
    def auth_handler(
        _path: str, _headers: dict[str, str], _body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        return 401, {"error": {"message": "Invalid API key", "type": "auth_error"}}

    with FakeOpenAIServer(auth_handler) as server:
        adapter = OpenAICompatibleAdapter(
            api_key="wrong-key",
            base_url=server.base_url,
            model_name="test-model",
            timeout_seconds=5.0,
        )
        with pytest.raises(openai.AuthenticationError):
            adapter.complete("hello")


def test_fake_server_malformed_responses_fail_closed() -> None:
    # 1. Empty choices
    with FakeOpenAIServer(lambda *_: (200, {"id": "x", "choices": []})) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="m", timeout_seconds=5.0
        )
        with pytest.raises(LLMResponseError, match="no choices"):
            adapter.complete("hello")

    # 2. Non-JSON text body with 200
    with FakeOpenAIServer(lambda *_: (200, "not-json-at-all")) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="m", timeout_seconds=5.0
        )
        with pytest.raises(LLMResponseError):
            adapter.complete("hello")

    # 3. Invalid schema JSON in assistant content
    with FakeOpenAIServer(
        lambda *_: (200, chat_completion_payload("not valid json"))
    ) as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, model_name="m", timeout_seconds=5.0
        )
        with pytest.raises(ValidationError):
            adapter.complete("hello", schema=QueryExpansionOutput)


def test_legacy_provider_model_warning_emitted_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    reset_legacy_warnings()
    with FakeOpenAIServer(lambda *_: (200, chat_completion_payload("ok"))) as server:
        monkeypatch.setattr(config, "external_provider_enabled", False)
        monkeypatch.setattr(config, "llm_api_key", None)
        monkeypatch.setattr(config, "llm_base_url", None)
        monkeypatch.setattr(
            config, "ollama_url", server.base_url.removesuffix("/v1")
        )
        monkeypatch.delenv("MESA_OLLAMA_URL", raising=False)
        monkeypatch.setattr(config, "llm_model", "ollama:qwen3:8b")

        with caplog.at_level(logging.WARNING, logger="MESA_AdapterFactory"):
            a1 = AdapterFactory.get_adapter()
            a2 = AdapterFactory.get_adapter()
        assert a1.complete("hi") == "ok"
        assert a2.complete("hi") == "ok"
        warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
        assert len(warnings) == 1
        assert "qwen3:8b" in warnings[0].getMessage()


def test_no_llm_configured_raises_d101_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "llm_model", "")
    assert AdapterFactory.is_llm_configured() is False
    with pytest.raises(LLMNotConfiguredError, match="no LLM configured"):
        AdapterFactory.get_adapter()
