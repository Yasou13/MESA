import os

from mem0 import Memory

os.environ["OPENAI_API_KEY"] = "sk-dummy"
os.environ["OPENAI_BASE_URL"] = "http://192.168.1.103:11434/v1"

mem0_config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "path": "/tmp/mem0_qdrant_test",
        },
    },
    "llm": {"provider": "openai", "config": {"model": "qwen3:8b"}},
    "embedder": {
        "provider": "ollama",
        "config": {"model": "nomic-embed-text:latest"},
    },
}

try:
    memory = Memory.from_config(mem0_config)
    print("Memory initialized.")
    memory.add(
        "Elena Vance is a quantum physicist.",
        user_id="test_user",
        metadata={"id": "test_ctx"},
    )
    print("Memory added successfully.")
    res = memory.search("Who is Elena Vance?", user_id="test_user")
    print("Search results:", res)
except Exception:
    import traceback

    traceback.print_exc()
