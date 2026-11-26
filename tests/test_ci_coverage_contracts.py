"""Network-free contracts exercised by the mandatory CI coverage gate.

These tests intentionally replace optional provider SDKs with small in-process
fakes.  The production adapters remain optional; the tests cover the behavior
MESA guarantees once an adapter is selected without reaching a provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
from pydantic import BaseModel

from mesa_client.client import (
    AsyncMesaClient,
    MesaAPIError,
    MesaClient,
)


class _ResultSchema(BaseModel):
    result: str


class _MockSchema(BaseModel):
    results: list[object]


def _choice(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _embedding(values: list[float], index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(data=[SimpleNamespace(embedding=values, index=index)])


def _install_openai_fake(monkeypatch):
    """Install an SDK-shaped fake after importing the OpenAI-compatible adapter."""
    from mesa_memory.adapter import openai_compat

    sync_client = MagicMock()
    async_client = MagicMock()
    fake_sdk = SimpleNamespace(
        OpenAI=Mock(return_value=sync_client),
        AsyncOpenAI=Mock(return_value=async_client),
        RateLimitError=type("RateLimitError", (Exception,), {}),
        APIConnectionError=type("APIConnectionError", (Exception,), {}),
        NotFoundError=type("NotFoundError", (Exception,), {}),
        BadRequestError=type("BadRequestError", (Exception,), {}),
    )
    monkeypatch.setattr(openai_compat, "openai", fake_sdk)
    return openai_compat, fake_sdk, sync_client, async_client


def test_openai_adapter_sync_contract_with_fake_sdk(monkeypatch) -> None:
    live, sdk, sync_client, _ = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key", base_url="http://fake")
    sync_client.chat.completions.create.side_effect = [
        _choice("plain response"),
        _choice('{"result": "ok"}'),
    ]
    sync_client.embeddings.create.side_effect = [
        _embedding([0.1, 0.2]),
        SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[2.0], index=1),
                SimpleNamespace(embedding=[1.0], index=0),
            ]
        ),
    ]

    assert adapter.complete("prompt") == "plain response"
    assert adapter.complete("prompt", schema=_ResultSchema).result == "ok"
    assert adapter.embed("one") == [0.1, 0.2]
    assert adapter.embed_batch(["one", "two"]) == [[1.0], [2.0]]
    assert sdk.OpenAI.call_args.kwargs["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_openai_adapter_async_contract_with_fake_sdk(monkeypatch) -> None:
    live, _, _, async_client = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key")
    async_client.chat.completions.create = AsyncMock(
        side_effect=[_choice("async"), _choice('{"result": "async schema"}')]
    )
    async_client.embeddings.create = AsyncMock(
        side_effect=[
            _embedding([0.4]),
            SimpleNamespace(data=[SimpleNamespace(embedding=[0.6], index=0)]),
        ]
    )

    assert await adapter.acomplete("prompt") == "async"
    assert (
        await adapter.acomplete("prompt", schema=_ResultSchema)
    ).result == "async schema"
    assert await adapter.aembed("one") == [0.4]
    assert await adapter.aembed_batch(["one"]) == [[0.6]]


def test_openai_adapter_not_found_fails_closed(monkeypatch) -> None:
    live, sdk, sync_client, _ = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key")
    sync_client.embeddings.create.side_effect = sdk.NotFoundError("missing model")

    with pytest.raises(RuntimeError, match="embedding model is unavailable"):
        adapter.embed("fallback")


def test_openai_adapter_sync_error_contract_without_retry_delay(monkeypatch) -> None:
    compat, sdk, sync_client, _ = _install_openai_fake(monkeypatch)
    adapter = compat.OpenAICompatibleAdapter(api_key="test-key")

    sync_client.chat.completions.create.side_effect = sdk.RateLimitError("limited")
    with pytest.raises(sdk.RateLimitError):
        adapter.complete("prompt")

    sync_client.embeddings.create.side_effect = sdk.APIConnectionError("offline")
    with pytest.raises(sdk.APIConnectionError):
        adapter.embed("text")
    with pytest.raises(sdk.APIConnectionError):
        adapter.embed_batch(["text"])

    monkeypatch.setattr(compat, "count_tokens", lambda *args, **kwargs: 6)
    assert adapter.get_token_count("one two") == 6


@pytest.mark.asyncio
async def test_openai_adapter_async_error_contract_without_retry_delay(
    monkeypatch,
) -> None:
    compat, sdk, _, async_client = _install_openai_fake(monkeypatch)
    adapter = compat.OpenAICompatibleAdapter(api_key="test-key")
    async_client.chat.completions.create = AsyncMock(
        side_effect=sdk.APIConnectionError("offline")
    )
    async_client.embeddings.create = AsyncMock(
        side_effect=sdk.RateLimitError("limited")
    )

    with pytest.raises(sdk.APIConnectionError):
        await adapter.acomplete("prompt")
    with pytest.raises(sdk.RateLimitError):
        await adapter.aembed("text")
    with pytest.raises(sdk.RateLimitError):
        await adapter.aembed_batch(["text"])


def test_adapter_factory_resolves_all_supported_provider_paths(monkeypatch) -> None:
    from mesa_memory.adapter import factory, openai_compat

    class FakeAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(openai_compat, "OpenAICompatibleAdapter", FakeAdapter)
    monkeypatch.setattr(factory.config, "llm_api_key", "configured-key")
    monkeypatch.setattr(factory.config, "llm_base_url", "http://provider")
    monkeypatch.setattr(factory.config, "external_provider_enabled", True)

    assert isinstance(
        factory.AdapterFactory.get_adapter("openai_compatible", model_name="m"),
        FakeAdapter,
    )
    assert isinstance(
        factory.AdapterFactory.get_adapter("openai", model_name="m"), FakeAdapter
    )
    assert isinstance(
        factory.AdapterFactory.get_adapter("ollama", model_name="m"), FakeAdapter
    )
    assert isinstance(
        factory.AdapterFactory.get_adapter("mock"), factory.DeterministicMockAdapter
    )

    monkeypatch.setattr(factory.config, "llm_model", "gpt-4o")
    assert isinstance(factory.AdapterFactory.get_adapter(), FakeAdapter)

    with pytest.raises(ValueError, match="Unknown LLM provider"):
        factory.AdapterFactory.get_adapter("unsupported")


def test_deterministic_mock_adapter_schema_and_embeddings(monkeypatch) -> None:
    from mesa_memory.adapter.factory import DeterministicMockAdapter
    from mesa_memory.config import config

    monkeypatch.setattr(config, "embedding_dimension", 4)
    adapter = DeterministicMockAdapter()
    assert adapter.complete("prompt") == "[MOCK] Deterministic response"
    assert adapter.complete("prompt", schema=_MockSchema) == _MockSchema(results=[])
    assert len(adapter.embed("text")) == 4
    assert adapter.embed_batch(["one", "two"])
    assert adapter.get_token_count("one two") == 2


@pytest.mark.asyncio
async def test_deterministic_mock_adapter_async_contract() -> None:
    from mesa_memory.adapter.factory import DeterministicMockAdapter

    adapter = DeterministicMockAdapter()
    assert await adapter.acomplete("prompt") == "[MOCK] Deterministic response"
    assert await adapter.aembed("text")
    assert await adapter.aembed_batch(["text"])


def test_tokenizer_contracts(monkeypatch) -> None:
    from mesa_memory.adapter import tokenizer

    encoding = SimpleNamespace(encode=lambda text: [1, 2, 3])
    monkeypatch.setattr(tokenizer, "_CL100K_ENCODING", encoding)
    assert tokenizer.count_tokens("text", "openai") == 3
    with pytest.raises(ValueError, match="Unknown adapter_type"):
        tokenizer.count_tokens("text", "unknown")


def test_tokenizer_fallback_contract(monkeypatch) -> None:
    import sys

    from mesa_memory.adapter import tokenizer

    class BrokenTokenizer:
        @staticmethod
        def from_pretrained(model_id):
            raise OSError("model is unavailable")

    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(AutoTokenizer=BrokenTokenizer)
    )
    assert tokenizer.count_tokens("one two three", "ollama", "missing") == 3
    assert tokenizer.enforce_context_limit("one", "openai", "", limit=10) is None
    with pytest.raises(Exception, match="exceeds limit"):
        tokenizer.enforce_context_limit("one two", "openai", "", limit=1)


def test_tokenizer_openai_falls_back_when_encoding_cache_is_unavailable(
    monkeypatch,
) -> None:
    from mesa_memory.adapter import tokenizer

    monkeypatch.setattr(tokenizer, "_CL100K_ENCODING", None)
    monkeypatch.setattr(
        tokenizer,
        "load_tiktoken_bpe",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert tokenizer.count_tokens("one two three", "openai") == len(
        "one two three".encode("utf-8")
    )


def test_tokenizer_openai_offline_exact(monkeypatch) -> None:
    import socket

    from mesa_memory.adapter import tokenizer

    monkeypatch.setattr(tokenizer, "_CL100K_ENCODING", None)

    def _block_connect(*args, **kwargs):
        raise OSError("Network call blocked in offline test")

    monkeypatch.setattr(socket, "create_connection", _block_connect)
    monkeypatch.setattr(socket.socket, "connect", _block_connect)

    # Online tiktoken cl100k_base yields 2 tokens for "hello world" ([15339, 1917])
    expected_tokens = 2
    assert tokenizer.count_tokens("hello world", "openai") == expected_tokens


def _response(status: int, payload: dict, *, version: str = "0.7.1") -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"X-API-Version": version},
        request=httpx.Request("POST", "http://mesa.test/request"),
    )


def test_sync_sdk_request_errors_and_context_manager() -> None:
    with MesaClient(
        base_url="http://mesa.test", api_key="key", max_retries=0
    ) as client:
        assert client._client.headers["X-API-Key"] == "key"
        client._client.request = Mock(
            side_effect=[
                _response(200, {"ok": True}, version="1.0.0"),
                _response(
                    500,
                    {"status_code": 500, "error": "internal", "detail": "failure"},
                ),
            ]
        )
        assert client._request("GET", "/health") == {"ok": True}
        with pytest.raises(MesaAPIError, match="internal"):
            client._request("GET", "/failure")


@pytest.mark.asyncio
async def test_async_sdk_request_errors_and_context_manager() -> None:
    async with AsyncMesaClient(
        base_url="http://mesa.test", api_key="key", max_retries=0
    ) as client:
        assert client._client.headers["X-API-Key"] == "key"
        client._client.request = AsyncMock(
            side_effect=[
                _response(200, {"ok": True}, version="1.0.0"),
                _response(
                    403, {"status_code": 403, "error": "forbidden", "detail": "denied"}
                ),
            ]
        )
        assert await client._request("GET", "/health") == {"ok": True}
        with pytest.raises(MesaAPIError, match="forbidden"):
            await client._request("POST", "/forbidden")
