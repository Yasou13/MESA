import json
from unittest.mock import patch, MagicMock

from mesa_memory.adapter.claude import ClaudeAdapter
from mesa_memory.adapter.ollama import OllamaAdapter
from mesa_memory.schema.cmb import CMB, ResourceCost


def test_claude_adapter_embed():
    mock_embedding = [0.1] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=mock_embedding)]

    with patch("mesa_memory.adapter.claude.openai.OpenAI") as MockOpenAI, \
         patch("mesa_memory.adapter.claude.openai.AsyncOpenAI"), \
         patch("mesa_memory.adapter.claude.anthropic.Anthropic"), \
         patch("mesa_memory.adapter.claude.anthropic.AsyncAnthropic"):
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        MockOpenAI.return_value = mock_client

        adapter = ClaudeAdapter(anthropic_api_key="test", openai_api_key="test")
        adapter._sync_openai = mock_client

        result = adapter.embed("test")
        assert isinstance(result, list)
        assert len(result) == 1536
        assert all(isinstance(v, float) for v in result)


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

    with patch("mesa_memory.adapter.claude.anthropic.Anthropic") as MockAnthropic, \
         patch("mesa_memory.adapter.claude.anthropic.AsyncAnthropic"), \
         patch("mesa_memory.adapter.claude.openai.OpenAI"), \
         patch("mesa_memory.adapter.claude.openai.AsyncOpenAI"):
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
