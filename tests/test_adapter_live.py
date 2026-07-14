import pytest
from unittest.mock import patch, MagicMock
from mesa_memory.adapter.live import OpenAICompatibleAdapter
from pydantic import BaseModel

class DummySchema(BaseModel):
    decision: str

def test_live_adapter_init_missing_key():
    with pytest.raises(ValueError, match="API key is required"):
        OpenAICompatibleAdapter(api_key=None)

def test_live_adapter_init_success():
    adapter = OpenAICompatibleAdapter(api_key="test-key", base_url="http://localhost")
    assert adapter.api_key == "test-key"
    assert adapter.base_url == "http://localhost"

def test_live_sanitize_json():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    text = "Here is the result: ```json\n{\"decision\": \"STORE\"}\n```"
    sanitized = adapter._sanitize_json(text)
    assert sanitized == '{"decision": "STORE"}'
    
    text2 = "Some text before {\"decision\": \"STORE\"} and some after"
    assert adapter._sanitize_json(text2) == '{"decision": "STORE"}'

def test_live_complete_sync():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="regular text"))]
    adapter._sync_client.chat.completions.create = MagicMock(return_value=mock_response)
    
    res = adapter.complete("prompt")
    assert res == "regular text"

def test_live_complete_sync_with_schema():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"decision": "STORE"}'))]
    adapter._sync_client.chat.completions.create = MagicMock(return_value=mock_response)
    
    res = adapter.complete("prompt", schema=DummySchema)
    assert isinstance(res, DummySchema)
    assert res.decision == "STORE"

@pytest.mark.asyncio
async def test_live_acomplete():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    
    async def mock_create(*args, **kwargs):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="async text"))]
        return mock_response
        
    adapter._async_client.chat.completions.create = mock_create
    res = await adapter.acomplete("prompt")
    assert res == "async text"

def test_live_embed_sync():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1, 0.2])]
    adapter._sync_client.embeddings.create = MagicMock(return_value=mock_response)
    
    res = adapter.embed("test")
    assert res == [0.1, 0.2]

@pytest.mark.asyncio
async def test_live_aembed():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    
    async def mock_create(*args, **kwargs):
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2])]
        return mock_response
        
    adapter._async_client.embeddings.create = mock_create
    res = await adapter.aembed("test")
    assert res == [0.1, 0.2]

def test_live_embed_batch():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    mock_response = MagicMock()
    
    data1 = MagicMock(embedding=[0.1])
    data1.index = 1
    data2 = MagicMock(embedding=[0.2])
    data2.index = 0
    
    mock_response.data = [data1, data2]
    adapter._sync_client.embeddings.create = MagicMock(return_value=mock_response)
    
    res = adapter.embed_batch(["text1", "text2"])
    assert res == [[0.2], [0.1]]

@pytest.mark.asyncio
async def test_live_aembed_batch():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    
    async def mock_create(*args, **kwargs):
        mock_response = MagicMock()
        data1 = MagicMock(embedding=[0.1])
        data1.index = 1
        data2 = MagicMock(embedding=[0.2])
        data2.index = 0
        mock_response.data = [data1, data2]
        return mock_response
        
    adapter._async_client.embeddings.create = mock_create
    res = await adapter.aembed_batch(["text1", "text2"])
    assert res == [[0.2], [0.1]]

@patch("mesa_memory.adapter.live.count_tokens")
def test_live_token_count(mock_count):
    mock_count.return_value = 5
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    assert adapter.get_token_count("hello world") == 5
