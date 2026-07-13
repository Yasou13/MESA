import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from mesa_memory.adapter.claude import ClaudeAdapter
from mesa_memory.adapter.ollama import OllamaAdapter


class DummySchema(BaseModel):
    decision: str


# ===================================================================
# ClaudeAdapter Tests
# ===================================================================


def test_claude_sync_complete():
    adapter = ClaudeAdapter(anthropic_api_key="sk-test", openai_api_key="sk-test")
    adapter._sync_anthropic.messages.create = MagicMock()
    adapter._sync_anthropic.messages.create.return_value.content = [
        MagicMock(text='{"decision": "STORE"}')
    ]

    res = adapter.complete("test", schema=DummySchema)
    assert isinstance(res, DummySchema)
    assert res.decision == "STORE"

    adapter._sync_anthropic.messages.create.return_value.content = [
        MagicMock(text="just text")
    ]
    res2 = adapter.complete("test")
    assert res2 == "just text"


@pytest.mark.asyncio
async def test_claude_async_complete():
    adapter = ClaudeAdapter(anthropic_api_key="sk-test")

    async def mock_create(*args, **kwargs):
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="test")]
        return mock_msg

    adapter._async_anthropic.messages.create = mock_create
    res = await adapter.acomplete("test")
    assert res == "test"


def test_claude_embed():
    adapter = ClaudeAdapter(anthropic_api_key="sk-test", openai_api_key="sk-test")
    adapter._sync_openai.embeddings.create = MagicMock()

    mock_data = MagicMock()
    mock_data.embedding = [0.1, 0.2]
    adapter._sync_openai.embeddings.create.return_value.data = [mock_data]

    res = adapter.embed("test")
    assert res == [0.1, 0.2]

    mock_data.index = 0
    res_batch = adapter.embed_batch(["test"])
    assert res_batch == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_claude_async_embed():
    adapter = ClaudeAdapter(anthropic_api_key="sk-test", openai_api_key="sk-test")

    async def mock_create(*args, **kwargs):
        mock_res = MagicMock()
        mock_data = MagicMock()
        mock_data.embedding = [0.1, 0.2]
        mock_data.index = 0
        mock_res.data = [mock_data]
        return mock_res

    adapter._async_openai.embeddings.create = mock_create
    res = await adapter.aembed("test")
    assert res == [0.1, 0.2]

    res_batch = await adapter.aembed_batch(["test"])
    assert res_batch == [[0.1, 0.2]]


@patch("mesa_memory.adapter.claude._local_embed")
@patch("mesa_memory.adapter.claude._local_embed_batch")
def test_claude_local_fallback(mock_batch, mock_single):
    adapter = ClaudeAdapter(anthropic_api_key="sk-test", openai_api_key=None)
    mock_single.return_value = [0.9]
    assert adapter.embed("test") == [0.9]

    mock_batch.return_value = [[0.9]]
    assert adapter.embed_batch(["test"]) == [[0.9]]


@patch("mesa_memory.adapter.claude.count_tokens")
def test_claude_token_count(mock_count):
    mock_count.return_value = 5
    adapter = ClaudeAdapter(anthropic_api_key="sk-test")
    assert adapter.get_token_count("hello world") == 5


# ===================================================================
# OllamaAdapter Tests
# ===================================================================


def test_ollama_complete():
    adapter = OllamaAdapter()
    adapter._ollama_client.generate = MagicMock(
        return_value={"response": "ollama resp"}
    )
    res = adapter.complete("test")
    assert res == "ollama resp"


def test_ollama_base_url_init():
    adapter = OllamaAdapter(base_url="http://custom.url")
    assert adapter.base_url == "http://custom.url"
    assert adapter._ollama_client is not None


@pytest.mark.skipif(
    os.getenv("GITHUB_ACTIONS") == "true", reason="CI ortamında Ollama yok"
)
def test_ollama_complete_schema():
    import sys

    mock_outlines = MagicMock()
    mock_gen = MagicMock(return_value=DummySchema(decision="STORE"))
    mock_outlines.generate.json.return_value = mock_gen

    adapter = OllamaAdapter()
    with patch.dict(sys.modules, {"outlines": mock_outlines}):
        res = adapter.complete("test", schema=DummySchema)
    assert res.decision == "STORE"


def test_ollama_embed():
    adapter = OllamaAdapter()
    adapter._ollama_client.embeddings = MagicMock(return_value={"embedding": [0.5]})
    assert adapter.embed("test") == [0.5]
    assert adapter.embed_batch(["test1", "test2"]) == [[0.5], [0.5]]


@pytest.mark.asyncio
async def test_ollama_async_methods():
    adapter = OllamaAdapter()
    adapter.complete = MagicMock(return_value="async res")
    adapter.embed = MagicMock(return_value=[0.3])
    adapter.embed_batch = MagicMock(return_value=[[0.3]])

    assert await adapter.acomplete("test") == "async res"
    assert await adapter.aembed("test") == [0.3]
    assert await adapter.aembed_batch(["test"]) == [[0.3]]


@patch("mesa_memory.adapter.ollama.count_tokens")
def test_ollama_token_count(mock_count):
    mock_count.return_value = 5
    adapter = OllamaAdapter()
    assert adapter.get_token_count("hello world") == 5


# ===================================================================
# Tokenizer Tests
# ===================================================================


def test_count_tokens_claude():
    from mesa_memory.adapter.tokenizer import count_tokens

    result = count_tokens("Hello, world!", adapter_type="claude")
    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_openai():
    from mesa_memory.adapter.tokenizer import count_tokens

    result = count_tokens("Hello, world!", adapter_type="openai")
    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_ollama_fallback():
    from mesa_memory.adapter.tokenizer import count_tokens

    # Invalid model → falls back to word-count estimate
    result = count_tokens(
        "Hello world test", adapter_type="ollama", model_id="nonexistent/model"
    )
    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_unknown_adapter_raises():
    from mesa_memory.adapter.tokenizer import count_tokens

    with pytest.raises(ValueError, match="Unknown adapter_type"):
        count_tokens("test", adapter_type="unknown")


def test_enforce_context_limit_passes():
    from mesa_memory.adapter.tokenizer import enforce_context_limit

    # Short text, high limit → no error
    enforce_context_limit("Hello", adapter_type="claude", model_id="", limit=1000)


def test_enforce_context_limit_raises():
    from mesa_memory.adapter.base import TokenBudgetExceededError
    from mesa_memory.adapter.tokenizer import enforce_context_limit

    with pytest.raises(TokenBudgetExceededError):
        enforce_context_limit(
            "word " * 5000,
            adapter_type="claude",
            model_id="",
            limit=10,
        )


# ===================================================================
# AdapterFactory Tests
# ===================================================================


def test_adapter_factory_ollama_explicit():
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.adapter.ollama import OllamaAdapter

    adapter = AdapterFactory.get_adapter(provider="ollama")
    assert isinstance(adapter, OllamaAdapter)


@patch("os.environ.get")
def test_adapter_factory_auto_detect_zero_cost(mock_env_get):
    from mesa_memory.adapter.factory import AdapterFactory
    from mesa_memory.adapter.ollama import OllamaAdapter

    def env_get_side_effect(key, default=None):
        if key == "MESA_OLLAMA_URL":
            return "http://localhost:11434"
        return default

    mock_env_get.side_effect = env_get_side_effect
    adapter = AdapterFactory.get_adapter(provider="auto")
    assert isinstance(adapter, OllamaAdapter)
