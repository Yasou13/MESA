import sys
import time
import uuid

from mesa_api.schemas import MemoryInsertRequest
from mesa_client.client import MesaClient
from mesa_client.langchain import MesaRetriever


def run_legal_simulation(
    base_url: str = "http://localhost:8000", api_key: str = ""
) -> None:
    """Run the legal assistant simulation as an integration test."""

    agent_id = f"legal-proxy-{uuid.uuid4().hex[:8]}"
    session_id = f"case-{uuid.uuid4().hex[:8]}"

    print(f"--- Starting Legal Proxy Simulation ---")
    print(f"Agent ID: {agent_id}")
    print(f"Session ID: {session_id}\n")

    client = MesaClient(base_url=base_url, api_key=api_key)

    # Initialize LangChain retriever
    retriever = MesaRetriever(
        client=client,
        agent_id=agent_id,
        session_id=session_id,
        search_limit=5,
    )

    # 1. Data Ingestion: Injecting temporal contradictions
    fact_1 = "Client sold the house in 2024"
    fact_2 = "Deed shows client owns house in 2025"

    print(f"Injecting Fact 1: {fact_1}")
    client.insert(
        MemoryInsertRequest(agent_id=agent_id, session_id=session_id, content=fact_1)
    )

    # Allow time for async processing (if needed by MESA)
    time.sleep(1)

    print(f"Injecting Fact 2 (Temporal Contradiction): {fact_2}")
    client.insert(
        MemoryInsertRequest(agent_id=agent_id, session_id=session_id, content=fact_2)
    )

    # Allow time for potential vectorization/DB triggers
    time.sleep(2)

    # 2. Query Execution via LangChain Adapter
    query = "Does the client own the house?"
    print(f"\nQuerying: '{query}'")

    # Call standard LangChain invoke method which routes to _get_relevant_documents
    docs = retriever.invoke(query)

    print("\n--- Retrieved Context ---")
    combined_context = []
    for i, doc in enumerate(docs):
        print(
            f"Document {i+1} [Score: {doc.metadata.get('score', 'N/A')}]: {doc.page_content}"
        )
        combined_context.append(doc.page_content.lower())

    full_text = " | ".join(combined_context)

    # 3. Validation Criteria
    # The output must demonstrate Bi-temporal gating (warning) OR correct resolution.
    has_warning = "unconsolidated" in full_text or "warning" in full_text
    has_fact_1 = "sold" in full_text and "2024" in full_text
    has_fact_2 = "owns" in full_text and "2025" in full_text

    print("\n--- Validation ---")

    try:
        # We assert that the system either warns about unconsolidated data
        # OR it successfully retrieves both conflicting facts so the agent can reason.
        assert has_warning or (has_fact_1 and has_fact_2), (
            "Integration Test Failed: System did not handle temporal contradiction correctly. "
            "It must either return an 'unconsolidated data' warning or return both conflicting facts."
        )
        print("Integration Test Passed: Temporal contradiction handled correctly.")
    except AssertionError as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    try:
        run_legal_simulation()
    except Exception as ex:
        print(f"Simulation terminated: {ex}")
        sys.exit(1)
