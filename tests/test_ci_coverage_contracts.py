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

from mesa_api.schemas import (
    MemoryInsertRequest,
    MemoryPurgeRequest,
    MemorySearchRequest,
)
from mesa_client.client import (
    AsyncMesaClient,
    MesaAPIError,
    MesaClient,
    MesaValidationError,
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
    """Install an SDK-shaped fake after importing the optional adapter."""
    from mesa_memory.adapter import live

    sync_client = MagicMock()
    async_client = MagicMock()
    fake_sdk = SimpleNamespace(
        OpenAI=Mock(return_value=sync_client),
        AsyncOpenAI=Mock(return_value=async_client),
        RateLimitError=type("RateLimitError", (Exception,), {}),
        APIConnectionError=type("APIConnectionError", (Exception,), {}),
        NotFoundError=type("NotFoundError", (Exception,), {}),
    )
    monkeypatch.setattr(live, "openai", fake_sdk)
    monkeypatch.setattr(live, "_OPENAI_RATE_LIMIT_ERRORS", (fake_sdk.RateLimitError,))
    monkeypatch.setattr(
        live, "_OPENAI_CONNECTION_ERRORS", (fake_sdk.APIConnectionError,)
    )
    monkeypatch.setattr(live, "_OPENAI_NOT_FOUND_ERRORS", (fake_sdk.NotFoundError,))
    return live, fake_sdk, sync_client, async_client


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


def test_openai_adapter_not_found_uses_local_embedding(monkeypatch) -> None:
    live, sdk, sync_client, _ = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key")
    sync_client.embeddings.create.side_effect = sdk.NotFoundError("missing model")
    monkeypatch.setattr("mesa_memory.adapter.claude._local_embed", lambda text: [0.9])

    assert adapter.embed("fallback") == [0.9]


def test_openai_adapter_sync_error_contract_without_retry_delay(monkeypatch) -> None:
    live, sdk, sync_client, _ = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key")

    sync_client.chat.completions.create.side_effect = sdk.RateLimitError("limited")
    with pytest.raises(sdk.RateLimitError):
        adapter.complete.__wrapped__(adapter, "prompt")

    sync_client.embeddings.create.side_effect = sdk.APIConnectionError("offline")
    with pytest.raises(sdk.APIConnectionError):
        adapter.embed.__wrapped__(adapter, "text")
    with pytest.raises(sdk.APIConnectionError):
        adapter.embed_batch.__wrapped__(adapter, ["text"])

    monkeypatch.setattr(live, "count_tokens", lambda *args, **kwargs: 6)
    assert adapter.get_token_count("one two") == 6


@pytest.mark.asyncio
async def test_openai_adapter_async_error_contract_without_retry_delay(
    monkeypatch,
) -> None:
    live, sdk, _, async_client = _install_openai_fake(monkeypatch)
    adapter = live.OpenAICompatibleAdapter(api_key="test-key")
    async_client.chat.completions.create = AsyncMock(
        side_effect=sdk.APIConnectionError("offline")
    )
    async_client.embeddings.create = AsyncMock(
        side_effect=sdk.RateLimitError("limited")
    )

    with pytest.raises(sdk.APIConnectionError):
        await adapter.acomplete.__wrapped__(adapter, "prompt")
    with pytest.raises(sdk.RateLimitError):
        await adapter.aembed.__wrapped__(adapter, "text")
    with pytest.raises(sdk.RateLimitError):
        await adapter.aembed_batch.__wrapped__(adapter, ["text"])


def test_adapter_factory_resolves_all_supported_provider_paths(monkeypatch) -> None:
    from mesa_memory.adapter import claude, factory, live, ollama

    class FakeAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(live, "OpenAICompatibleAdapter", FakeAdapter)
    monkeypatch.setattr(claude, "ClaudeAdapter", FakeAdapter)
    monkeypatch.setattr(ollama, "OllamaAdapter", FakeAdapter)
    monkeypatch.setattr(factory.config, "llm_api_key", "configured-key")
    monkeypatch.setattr(factory.config, "anthropic_api_key", "anthropic-key")
    monkeypatch.setattr(factory.config, "llm_base_url", "http://provider")
    monkeypatch.setattr(factory.config, "llm_model_name", "model")

    assert isinstance(
        factory.AdapterFactory.get_adapter("openai_compatible"), FakeAdapter
    )
    assert isinstance(factory.AdapterFactory.get_adapter("claude"), FakeAdapter)
    assert isinstance(factory.AdapterFactory.get_adapter("ollama"), FakeAdapter)
    assert isinstance(
        factory.AdapterFactory.get_adapter("mock"), factory.DeterministicMockAdapter
    )

    monkeypatch.setenv("MESA_OLLAMA_URL", "http://ollama")
    assert isinstance(factory.AdapterFactory.get_adapter("auto"), FakeAdapter)
    monkeypatch.delenv("MESA_OLLAMA_URL")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert isinstance(factory.AdapterFactory.get_adapter("auto"), FakeAdapter)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(factory.config, "llm_api_key", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    assert isinstance(factory.AdapterFactory.get_adapter("auto"), FakeAdapter)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(factory.config, "anthropic_api_key", "")
    assert isinstance(
        factory.AdapterFactory.get_adapter("auto"), factory.DeterministicMockAdapter
    )

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


def test_claude_adapter_contract_with_fake_optional_sdks(monkeypatch) -> None:
    from mesa_memory.adapter import claude

    sync_anthropic, async_anthropic = MagicMock(), MagicMock()
    sync_openai, async_openai = MagicMock(), MagicMock()
    monkeypatch.setattr(
        claude,
        "anthropic",
        SimpleNamespace(
            Anthropic=Mock(return_value=sync_anthropic),
            AsyncAnthropic=Mock(return_value=async_anthropic),
        ),
    )
    monkeypatch.setattr(
        claude,
        "_openai_module",
        SimpleNamespace(
            OpenAI=Mock(return_value=sync_openai),
            AsyncOpenAI=Mock(return_value=async_openai),
        ),
    )
    sync_anthropic.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text='{"result": "claude"}')]
    )
    sync_openai.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.3], index=0)]
    )
    adapter = claude.ClaudeAdapter("anthropic-key", "openai-key")

    assert adapter.complete("prompt", schema=_ResultSchema).result == "claude"
    assert adapter.embed("text") == [0.3]
    assert adapter.embed_batch(["text"]) == [[0.3]]
    monkeypatch.setattr(claude, "count_tokens", lambda *args, **kwargs: 4)
    assert adapter.get_token_count("text") == 4


@pytest.mark.asyncio
async def test_claude_adapter_async_and_sync_local_fallback_contract(
    monkeypatch,
) -> None:
    from mesa_memory.adapter import claude

    sync_anthropic, async_anthropic = MagicMock(), MagicMock()
    async_anthropic.messages.create = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(text="async claude")])
    )
    monkeypatch.setattr(
        claude,
        "anthropic",
        SimpleNamespace(
            Anthropic=Mock(return_value=sync_anthropic),
            AsyncAnthropic=Mock(return_value=async_anthropic),
        ),
    )
    monkeypatch.setattr(claude, "_openai_module", None)
    monkeypatch.setattr(claude, "_local_embed", lambda text: [0.7])
    monkeypatch.setattr(
        claude, "_local_embed_batch", lambda texts: [[0.8] for _ in texts]
    )
    adapter = claude.ClaudeAdapter("anthropic-key")

    assert await adapter.acomplete("prompt") == "async claude"
    assert adapter.embed("text") == [0.7]
    assert adapter.embed_batch(["one", "two"]) == [[0.8], [0.8]]


@pytest.mark.asyncio
async def test_claude_adapter_async_openai_embedding_contract(monkeypatch) -> None:
    from mesa_memory.adapter import claude

    sync_anthropic, async_anthropic = MagicMock(), MagicMock()
    async_anthropic.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(text='{"result": "async"}')]
        )
    )
    async_openai = MagicMock()
    async_openai.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.2], index=0)])
    )
    monkeypatch.setattr(
        claude,
        "anthropic",
        SimpleNamespace(
            Anthropic=Mock(return_value=sync_anthropic),
            AsyncAnthropic=Mock(return_value=async_anthropic),
        ),
    )
    monkeypatch.setattr(
        claude,
        "_openai_module",
        SimpleNamespace(
            OpenAI=Mock(return_value=MagicMock()),
            AsyncOpenAI=Mock(return_value=async_openai),
        ),
    )
    adapter = claude.ClaudeAdapter("anthropic-key", "openai-key")

    assert (await adapter.acomplete("prompt", schema=_ResultSchema)).result == "async"
    assert await adapter.aembed("one") == [0.2]
    assert await adapter.aembed_batch(["one"]) == [[0.2]]


def test_ollama_and_tokenizer_contracts_with_fake_sdk(monkeypatch) -> None:
    from mesa_memory.adapter import ollama, tokenizer

    client = MagicMock()
    client.generate.side_effect = [
        {"response": "plain"},
        {"response": '{"result": "structured"}'},
    ]
    client.embeddings.return_value = {"embedding": [0.5]}
    monkeypatch.setattr(
        ollama, "ollama", SimpleNamespace(Client=Mock(return_value=client))
    )
    adapter = ollama.OllamaAdapter(model="fake", base_url="http://fake")
    assert adapter.complete("prompt") == "plain"
    assert adapter.complete("prompt", schema=_ResultSchema).result == "structured"
    assert adapter.embed_batch(["one", "two"]) == [[0.5], [0.5]]
    monkeypatch.setattr(ollama, "count_tokens", lambda *args, **kwargs: 3)
    assert adapter.get_token_count("one two") == 3

    encoding = SimpleNamespace(encode=lambda text: [1, 2, 3])
    monkeypatch.setattr(tokenizer.tiktoken, "get_encoding", lambda name: encoding)
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


def test_tokenizer_openai_falls_back_when_encoding_cache_is_unavailable(monkeypatch) -> None:
    from mesa_memory.adapter import tokenizer

    monkeypatch.setattr(
        tokenizer.tiktoken,
        "get_encoding",
        lambda _name: (_ for _ in ()).throw(OSError("offline")),
    )
    assert tokenizer.count_tokens("one two three", "openai") == 3


def _response(status: int, payload: dict, *, version: str = "0.7.0") -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"X-API-Version": version},
        request=httpx.Request("POST", "http://mesa.test/request"),
    )


def test_sync_sdk_requests_public_operations_and_validation() -> None:
    client = MesaClient(base_url="http://mesa.test", api_key="key", max_retries=0)
    client._client.request = Mock(
        side_effect=[
            _response(
                202,
                {
                    "status": "queued",
                    "log_id": 1,
                    "agent_id": "agent",
                    "processing_mode": "async",
                },
            ),
            _response(200, {"context": "ctx", "retrieved_nodes": [], "metrics": {}}),
            _response(200, {"status": "purged", "deleted_records_count": 2}),
        ]
    )
    try:
        assert (
            client.insert(
                MemoryInsertRequest(
                    agent_id="agent", session_id="session", content="text"
                )
            ).log_id
            == 1
        )
        assert (
            client.search(
                MemorySearchRequest(
                    agent_id="agent", session_id="session", query="text"
                )
            ).context
            == "ctx"
        )
        assert (
            client.purge(
                MemoryPurgeRequest(agent_id="agent", scope="agent", scope_id="agent")
            ).deleted_records_count
            == 2
        )

        client._request = Mock(return_value={})
        with pytest.raises(MesaValidationError):
            client.insert(
                MemoryInsertRequest(
                    agent_id="agent", session_id="session", content="text"
                )
            )
        with pytest.raises(MesaValidationError):
            client.purge(
                MemoryPurgeRequest(agent_id="agent", scope="agent", scope_id="agent")
            )
    finally:
        client.close()


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
async def test_async_sdk_requests_public_operations_and_errors() -> None:
    client = AsyncMesaClient(base_url="http://mesa.test", api_key="key", max_retries=0)
    client._client.request = AsyncMock(
        side_effect=[
            _response(
                202,
                {
                    "status": "queued",
                    "log_id": 1,
                    "agent_id": "agent",
                    "processing_mode": "async",
                },
            ),
            _response(200, {"context": "", "retrieved_nodes": [], "metrics": {}}),
            _response(200, {"status": "purged", "deleted_records_count": 0}),
            _response(
                403, {"status_code": 403, "error": "forbidden", "detail": "denied"}
            ),
        ]
    )
    try:
        assert (
            await client.insert(
                MemoryInsertRequest(
                    agent_id="agent", session_id="session", content="text"
                )
            )
        ).status == "queued"
        assert (
            await client.search(
                MemorySearchRequest(
                    agent_id="agent", session_id="session", query="text"
                )
            )
        ).retrieved_nodes == []
        assert (
            await client.purge(
                MemoryPurgeRequest(agent_id="agent", scope="agent", scope_id="agent")
            )
        ).status == "purged"
        with pytest.raises(MesaAPIError, match="forbidden"):
            await client._request("POST", "/forbidden")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_async_sdk_context_manager_and_validation_error() -> None:
    async with AsyncMesaClient(
        base_url="http://mesa.test", api_key="key", max_retries=0
    ) as client:
        assert client._client.headers["X-API-Key"] == "key"
        client._request = AsyncMock(return_value={})
        with pytest.raises(MesaValidationError):
            await client.insert(
                MemoryInsertRequest(
                    agent_id="agent", session_id="session", content="text"
                )
            )
        with pytest.raises(MesaValidationError):
            await client.purge(
                MemoryPurgeRequest(agent_id="agent", scope="agent", scope_id="agent")
            )
