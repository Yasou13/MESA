from typing import List, Sequence, Optional, Iterator

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.stores import BaseStore
from pydantic import ConfigDict, Field

from mesa_api.schemas import MemorySearchRequest, MemoryInsertRequest
from mesa_client.client import MesaClient


class MesaRetriever(BaseRetriever):
    """LangChain Retriever adapter for the MESA memory system.

    Provides a seamless integration for LangChain agents to query
    the MESA memory layer while enforcing strict tenant isolation.
    """

    client: MesaClient = Field(
        description="Configured instance of the synchronous MesaClient"
    )
    agent_id: str = Field(description="Tenant identifier enforcing row-level isolation")
    session_id: str = Field(
        default="langchain-session",
        description="Session scope within the agent tenant",
    )
    search_limit: int = Field(
        default=5,
        description="Maximum number of documents to retrieve per query",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        """Retrieve matching memory nodes from MESA and map to LangChain Documents."""
        request = MemorySearchRequest(
            agent_id=self.agent_id,
            session_id=self.session_id,
            query=query,
            limit=self.search_limit,
        )

        response = self.client.search(request)

        documents = []
        for item in response.retrieved_nodes:
            doc = Document(
                page_content=item.content_payload or item.entity_name,
                metadata={
                    "node_id": item.node_id,
                    "score": item.score,
                    "content_hash": item.content_hash,
                    "agent_id": item.agent_id,
                },
            )
            documents.append(doc)

        return documents


class MesaStore(BaseStore[str, str]):
    """LangChain BaseStore adapter for MESA."""

    client: MesaClient = Field(
        description="Configured instance of the synchronous MesaClient"
    )
    agent_id: str = Field(description="Tenant identifier enforcing row-level isolation")
    session_id: str = Field(
        default="langchain-session",
        description="Session scope within the agent tenant",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def mget(self, keys: Sequence[str]) -> list[Optional[str]]:
        """Retrieve memories by querying MESA for each key."""
        results: list[Optional[str]] = []
        for key in keys:
            from mesa_api.schemas import MemorySearchRequest
            req = MemorySearchRequest(
                agent_id=self.agent_id,
                session_id=self.session_id,
                query=key,
                limit=1
            )
            try:
                resp = self.client.search(req)
                if resp.retrieved_nodes:
                    results.append(resp.retrieved_nodes[0].content_payload or resp.retrieved_nodes[0].entity_name)
                else:
                    results.append(None)
            except Exception:
                results.append(None)
        return results

    def mset(self, key_value_pairs: Sequence[tuple[str, str]]) -> None:
        """Insert a batch of memories into MESA."""
        for key, value in key_value_pairs:
            req = MemoryInsertRequest(
                agent_id=self.agent_id,
                session_id=self.session_id,
                content=value,
                metadata={"langchain_key": key}
            )
            self.client.insert(req)

    def mdelete(self, keys: Sequence[str]) -> None:
        """Not directly supported by key in MESA. Requires purge by session or agent."""
        pass

    def yield_keys(self, *, prefix: Optional[str] = None) -> Iterator[str]:
        """Not supported."""
        yield from []
