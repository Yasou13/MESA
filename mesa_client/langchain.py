from typing import List

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from mesa_api.schemas import MemorySearchRequest
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
        for item in response.results:
            doc = Document(
                page_content=item.entity_name,
                metadata={
                    "node_id": item.node_id,
                    "score": item.score,
                    "content_hash": item.content_hash,
                    "agent_id": item.agent_id,
                },
            )
            documents.append(doc)

        return documents
