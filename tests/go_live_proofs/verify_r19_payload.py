import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mesa_api.schemas import MemorySearchResponse, SearchResultItem
from mesa_client.client import MesaClient
from mesa_client.langchain import MesaRetriever


def main():
    agent_id = "test_agent_r19"
    test_payload = (
        "This is a very long and specific payload for R19 testing that must not be truncated or lost. "
        * 10
    )

    # Create a dummy client
    client = MesaClient(base_url="http://localhost:8001", api_key="dummy")

    # Mock the search method to return a response with content_payload
    def mock_search(request):
        return MemorySearchResponse(
            retrieved_nodes=[
                SearchResultItem(
                    node_id="test_node_1",
                    agent_id=agent_id,
                    entity_name="R19 testing",
                    content_payload=test_payload,
                    score=0.99,
                    content_hash="mock_hash",
                )
            ]
        )

    client.search = mock_search

    print("Searching memory via mocked client...")
    retriever = MesaRetriever(client=client, agent_id=agent_id)

    results = retriever.invoke("R19 testing")
    assert len(results) > 0, "No results returned"

    returned_content = results[0].page_content
    print(f"Returned content length: {len(returned_content)}")
    print(f"Original content length: {len(test_payload)}")

    assert (
        returned_content == test_payload
    ), "Payload was truncated or lost (R-19 BUG IS STILL PRESENT)"
    print("SUCCESS: Payload is fully intact (R-19 is FIXED).")


if __name__ == "__main__":
    main()
