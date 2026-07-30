import logging
from typing import Any

from mesa_evals.benchmark_adapters.base import BaseMemoryClient, QueryResult

try:
    from letta import Letta as LettaClient

    LETTA_AVAILABLE = True
except ImportError:
    LettaClient = None
    LETTA_AVAILABLE = False

logger = logging.getLogger("MESA_LettaAdapter")


class LettaAdapter(BaseMemoryClient):
    """Adapter for the Letta (formerly MemGPT) memory system.

    Implements the standard BaseMemoryClient interface for Apple-to-Apple
    comparisons in the MESA evaluation framework.
    """

    def __init__(self, base_url: str = "http://localhost:8283"):
        self.base_url = base_url
        self.client: Any = None
        # Maps agent_id -> Letta internal agent ID
        self._agent_map: dict[str, str] = {}

    async def initialize(self) -> None:
        if not LETTA_AVAILABLE:
            raise ImportError(
                "Letta library is not installed. Install with: pip install letta"
            )
        self.client = LettaClient(base_url=self.base_url)

    async def shutdown(self) -> None:
        self.client = None
        self._agent_map.clear()

    def _ensure_agent(self, agent_id: str) -> str:
        """Creates a fresh agent or retrieves an existing one for the given agent_id."""
        if agent_id in self._agent_map:
            return self._agent_map[agent_id]

        if not self.client:
            raise RuntimeError("LettaAdapter not initialized")

        # Create new agent for this agent_id
        agent_name = f"mesa_eval_{agent_id}"

        # Try to find existing first
        try:
            agents = self.client.agents.list()
            for agent in agents:
                if getattr(agent, "name", None) == agent_name:
                    self._agent_map[agent_id] = agent.id
                    return agent.id  # type: ignore[no-any-return]
        except Exception:
            pass

        # Create new
        try:
            agent = self.client.agents.create(
                name=agent_name,
                memory_blocks=[],
                description="MESA benchmark evaluation agent",
            )
            letta_id = agent.id
        except Exception:
            # Fallback for older API
            try:
                agent = self.client.create_agent(
                    name=agent_name,
                    description="MESA benchmark evaluation agent",
                )
                letta_id = getattr(agent, "id", getattr(agent, "agent_id", None))
            except Exception as e:
                raise RuntimeError(f"Failed to create Letta agent: {e}")

        self._agent_map[agent_id] = letta_id
        return letta_id  # type: ignore[no-any-return]

    async def add_memory(
        self, content: str, *, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> str:
        self._validate_agent_id(agent_id)
        letta_id = self._ensure_agent(agent_id)

        meta = metadata or {}
        try:
            self.client.agents.archival.create(
                agent_id=letta_id,
                text=content,
                metadata=meta,
            )
        except Exception:
            # Fallback for older Letta API
            self.client.insert_archival_memory(
                agent_id=letta_id,
                memory=content,
            )

        return str(meta.get("entry_id", "unknown"))

    async def query(
        self, question: str, *, agent_id: str, limit: int = 5
    ) -> QueryResult:
        self._validate_agent_id(agent_id)
        letta_id = self._ensure_agent(agent_id)

        try:
            try:
                results = self.client.agents.archival.list(
                    agent_id=letta_id,
                    query=question,
                    limit=limit,
                )
            except Exception:
                results = self.client.get_archival_memory(
                    agent_id=letta_id,
                    query=question,
                    limit=limit,
                )

            chunks = []
            if results:
                for r in results:
                    text = getattr(r, "text", None) or getattr(r, "content", "")
                    meta = getattr(r, "metadata", {}) or {}
                    if text:
                        chunks.append({"content": str(text), "metadata": meta})

            if not chunks:
                return QueryResult.empty()

            context = "\n".join([str(c["content"]) for c in chunks])
            return QueryResult(
                context=context, chunks=chunks, total_chunks=len(chunks), error=None
            )

        except Exception as exc:
            logger.error("Letta query failed: %s", exc)
            return QueryResult.from_error(str(exc))

    async def clear_memory(self, *, agent_id: str) -> int:
        self._validate_agent_id(agent_id)

        letta_id = self._agent_map.get(agent_id)
        if not letta_id:
            return 0

        try:
            self.client.agents.delete(letta_id)
        except Exception:
            try:
                self.client.delete_agent(letta_id)
            except Exception:
                pass

        self._agent_map.pop(agent_id, None)
        return 1
