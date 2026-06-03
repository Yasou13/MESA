from unittest.mock import MagicMock, patch

from mesa_memory.adapter.claude import ClaudeAdapter
from mesa_memory.adapter.ollama import OllamaAdapter
from tests.fixtures.vectors import VEC_BASE, VEC_BASE_384, VEC_BASE_1536


def test_claude_adapter_embed():
    mock_embedding = VEC_BASE_1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=mock_embedding)]

    with (
        patch("mesa_memory.adapter.claude._openai_module") as mock_openai,
        patch("mesa_memory.adapter.claude.anthropic.Anthropic"),
        patch("mesa_memory.adapter.claude.anthropic.AsyncAnthropic"),
    ):
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
    mock_embedding = VEC_BASE_384  # all-MiniLM-L6-v2 produces 384-dim vectors

    with (
        patch(
            "mesa_memory.adapter.claude._local_embed", return_value=mock_embedding
        ) as mock_local,
        patch("mesa_memory.adapter.claude.anthropic.Anthropic"),
        patch("mesa_memory.adapter.claude.anthropic.AsyncAnthropic"),
    ):

        adapter = ClaudeAdapter(anthropic_api_key="test", openai_api_key=None)
        assert adapter._sync_openai is None

        result = adapter.embed("test query")
        assert isinstance(result, list)
        assert len(result) == 384
        mock_local.assert_called_once_with("test query")


def test_ollama_adapter_embed():
    mock_embedding = VEC_BASE

    with patch("mesa_memory.adapter.ollama.ollama.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.embeddings.return_value = {"embedding": mock_embedding}
        MockClient.return_value = mock_client

        adapter = OllamaAdapter(model="mistral", embedding_model="nomic-embed-text")

        result = adapter.embed("test")
        assert isinstance(result, list)
        assert len(result) == 768
        assert all(isinstance(v, float) for v in result)
