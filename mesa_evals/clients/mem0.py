import asyncio
import logging
import os
import uuid
from typing import Any

from mem0 import Memory

from mesa_evals.clients.base import BaseMemoryClient, QueryResult
from mesa_memory.config import config

logger = logging.getLogger("MESA_Mem0Client")


class Mem0Client(BaseMemoryClient):
    """Authentic wrapper for the Mem0 baseline package.

    All synchronous Mem0 operations are offloaded via asyncio.to_thread()
    to prevent event loop blocking and ensure fair latency comparison.
    """

    def __init__(self, adapter: Any = None):
        mem0_config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {"path": "./storage/benchmark_mem0_qdrant"},
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": config.llm_model_name or "gpt-4o-mini",
                    "temperature": 0.0,
                    "max_tokens": 1500,
                    "api_key": os.environ.get("OPENAI_API_KEY", ""),
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": "sentence-transformers/all-MiniLM-L6-v2"},
            },
        }
        self.memory = Memory.from_config(mem0_config)

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def add_memory(
        self, content: str, *, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> str:
        self._validate_agent_id(agent_id)
        try:
            # Offload synchronous Mem0 call to thread pool
            await asyncio.to_thread(
                self.memory.add,
                content,
                filters={"user_id": agent_id},
                metadata=metadata,
            )
            return str(uuid.uuid4())
        except Exception as e:
            logger.error(f"Mem0 add failed: {e}")
            return ""

    async def query(
        self, question: str, *, agent_id: str, limit: int = 5
    ) -> QueryResult:
        self._validate_agent_id(agent_id)
        try:
            # Offload synchronous Mem0 call to thread pool
            results = await asyncio.to_thread(
                self.memory.search,
                query=question,
                filters={"user_id": agent_id},
                limit=limit,
            )
            if not results:
                return QueryResult.empty()

            chunks = []
            context_parts = []
            # mem0 search returns a list of dictionaries with "memory" or "text" and "id", "metadata"
            for hit in results:
                text = hit.get("memory", "") or hit.get("text", "")
                if text:
                    context_parts.append(text)
                    chunks.append(
                        {
                            "node_id": hit.get("id", ""),
                            "content": text,
                            "metadata": hit.get("metadata", {}),
                        }
                    )

            if not context_parts:
                return QueryResult.empty()

            return QueryResult(
                context="\n---\n".join(context_parts),
                chunks=chunks,
                total_chunks=len(chunks),
                error=None,
            )
        except Exception as e:
            logger.error(f"Mem0 query failed: {e}")
            return QueryResult.from_error(str(e))

    async def clear_memory(self, *, agent_id: str) -> int:
        self._validate_agent_id(agent_id)
        try:
            # Offload synchronous Mem0 call to thread pool
            await asyncio.to_thread(
                self.memory.delete_all, filters={"user_id": agent_id}
            )
            return 1
        except Exception as e:
            logger.error(f"Mem0 clear failed: {e}")
            return 0
