import logging
from typing import Any

from mesa_evals.benchmark_adapters.base import BaseMemoryClient, QueryResult

try:
    from zep_cloud.client import Zep as ZepCloud
    from zep_cloud.types import Message as ZepCloudMessage

    Zep: Any = ZepCloud
    Message: Any = ZepCloudMessage
    ZEP_AVAILABLE = True
except ImportError:
    try:
        from zep_python import ZepClient as ZepPython
        from zep_python.message import Message as ZepPythonMessage

        Zep = ZepPython
        Message = ZepPythonMessage
        ZEP_AVAILABLE = True
    except ImportError:
        Zep = None
        Message = None
        ZEP_AVAILABLE = False

logger = logging.getLogger("MESA_ZepAdapter")


class ZepAdapter(BaseMemoryClient):
    """Adapter for the Zep memory system.

    Implements the standard BaseMemoryClient interface for Apple-to-Apple
    comparisons in the MESA evaluation framework.
    """

    def __init__(self, api_key: str = "", base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.client: Any = None

    async def initialize(self) -> None:
        if not ZEP_AVAILABLE:
            raise ImportError(
                "Zep library is not installed. Install with: pip install zep-cloud"
            )

        if self.base_url:
            self.client = Zep(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = Zep(api_key=self.api_key)

    async def shutdown(self) -> None:
        self.client = None

    def _ensure_session(self, session_id: str) -> None:
        if not self.client:
            raise RuntimeError("ZepAdapter not initialized")
        try:
            self.client.memory.add_session(
                session_id=session_id,
                metadata={"benchmark": True},
            )
        except Exception:
            pass  # Session may already exist

    async def add_memory(
        self, content: str, *, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> str:
        self._validate_agent_id(agent_id)
        self._ensure_session(agent_id)

        meta = metadata or {}

        messages = [
            Message(
                role="user",
                content=content,
                metadata=meta,
            )
        ]

        try:
            self.client.memory.add(
                session_id=agent_id,
                messages=messages,
            )
        except Exception as exc:
            logger.error("Zep add_memory failed: %s", exc)

        return str(meta.get("entry_id", "unknown"))

    async def query(
        self, question: str, *, agent_id: str, limit: int = 5
    ) -> QueryResult:
        self._validate_agent_id(agent_id)
        self._ensure_session(agent_id)

        try:
            results = self.client.memory.search(
                session_id=agent_id,
                text=question,
                search_type="mmr",
                limit=limit,
            )

            chunks = []
            if results:
                for r in results:
                    content = getattr(r, "content", None) or getattr(r, "summary", "")
                    meta = getattr(r, "metadata", {}) or {}
                    if content:
                        chunks.append({"content": str(content), "metadata": meta})

            if not chunks:
                return QueryResult.empty()

            context = "\n".join([str(c["content"]) for c in chunks])
            return QueryResult(
                context=context, chunks=chunks, total_chunks=len(chunks), error=None
            )

        except Exception as exc:
            logger.error("Zep query failed: %s", exc)
            return QueryResult.from_error(str(exc))

    async def clear_memory(self, *, agent_id: str) -> int:
        self._validate_agent_id(agent_id)
        if not self.client:
            return 0

        try:
            self.client.memory.delete(session_id=agent_id)
        except Exception:
            pass

        self._ensure_session(agent_id)
        return 1
