import json
from unittest.mock import patch, MagicMock

from mesa_memory.adapter.claude import ClaudeAdapter
from mesa_memory.adapter.ollama import OllamaAdapter
from mesa_memory.schema.cmb import CMB


def test_claude_adapter_embed():
    mock_embedding = [0.1] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=mock_embedding)]

    with patch("mesa_memory.adapter.claude._openai_module") as mock_openai, patch(
        "mesa_memory.adapter.claude.anthropic.Anthropic"
    ), patch("mesa_memory.adapter.claude.anthropic.AsyncAnthropic"):
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client
        mock_openai.AsyncOpenAI.return_value = MagicMock()

        adapter = ClaudeAdapter(anthropic_api_key="test", openai_api_key="test")
        adapter._sync_openai = mock_client

        result = adapter.embed("test")
        assert isinstance(result, list)
        assert len(result) == 1536
        assert all(isinstance(v, float) for v in result)


def test_claude_adapter_local_embed_fallback():
    """When no OpenAI key is provided, embed() should fall back to local model."""
    mock_embedding = [0.05] * 384  # all-MiniLM-L6-v2 produces 384-dim vectors

    with patch(
        "mesa_memory.adapter.claude._local_embed", return_value=mock_embedding
    ) as mock_local, patch("mesa_memory.adapter.claude.anthropic.Anthropic"), patch(
        "mesa_memory.adapter.claude.anthropic.AsyncAnthropic"
    ):

        adapter = ClaudeAdapter(anthropic_api_key="test", openai_api_key=None)
        assert adapter._sync_openai is None

        result = adapter.embed("test query")
        assert isinstance(result, list)
        assert len(result) == 384
        mock_local.assert_called_once_with("test query")


def test_ollama_adapter_embed():
    mock_embedding = [0.2] * 768

    with patch("mesa_memory.adapter.ollama.ollama.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.embeddings.return_value = {"embedding": mock_embedding}
        MockClient.return_value = mock_client

        adapter = OllamaAdapter(model="mistral", embedding_model="nomic-embed-text")

        result = adapter.embed("test")
        assert isinstance(result, list)
        assert len(result) == 768
        assert all(isinstance(v, float) for v in result)


def test_adapter_complete_with_schema():
    cmb_data = {
        "content_payload": "test memory",
        "source": "agent",
        "performative": "assert",
        "resource_cost": {"token_count": 50, "latency_ms": 12.5},
    }
    mock_json_response = json.dumps(cmb_data)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=mock_json_response)]

    with patch(
        "mesa_memory.adapter.claude.anthropic.Anthropic"
    ) as MockAnthropic, patch(
        "mesa_memory.adapter.claude.anthropic.AsyncAnthropic"
    ), patch(
        "mesa_memory.adapter.claude._openai_module"
    ) as mock_openai:
        mock_openai.OpenAI.return_value = MagicMock()
        mock_openai.AsyncOpenAI.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        MockAnthropic.return_value = mock_client

        adapter = ClaudeAdapter(anthropic_api_key="test", openai_api_key="test")
        adapter._sync_anthropic = mock_client

        result = adapter.complete("Generate a CMB", schema=CMB)
        assert isinstance(result, CMB)
        assert result.content_payload == "test memory"
        assert result.source == "agent"
        assert result.resource_cost.token_count == 50
