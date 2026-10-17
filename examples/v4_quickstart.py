"""Neutral, scoped V4 example; requires model-enabled combined V4 server/ACL and reviewed providers."""

import os

from mesa_client import MesaV4Client


def main() -> None:
    with MesaV4Client(
        os.environ.get("MESA_BASE_URL", "http://127.0.0.1:18080"),
        api_key=os.environ["MESA_API_KEY"],
    ) as client:
        client.create_workspace(
            tenant_id="docs-tenant",
            workspace_id="docs-workspace",
            tenant_name="Documentation demo",
            workspace_name="Disposable workspace",
        )
        client.create_dataset(
            tenant_id="docs-tenant",
            workspace_id="docs-workspace",
            dataset_id="docs-dataset",
            dataset_name="Disposable dataset",
        )
        session = client.start_session(
            tenant_id="docs-tenant",
            workspace_id="docs-workspace",
            dataset_ids=["docs-dataset"],
            agent_id="docs-agent",
        )
        accepted = client.insert(
            session_id=session["session_id"],
            dataset_id="docs-dataset",
            document_id="docs-neutral-document",
            revision_id="docs-revision-1",
            chunk_id="docs-chunk-1",
            title="Neutral documentation sample",
            source_ref="demo://archive-hours",
            content="The demo archive opens at nine.",
            idempotency_key="docs-archive-hours-1",
        )
        terminal = client.wait_until_committed(accepted["mutation_id"])
        if terminal["state"] != "COMMITTED":
            raise RuntimeError(f"Mutation failed: {terminal['state']}")
        result = client.search(
            session_id=session["session_id"], query="demo archive", limit=5
        )
        if not any(
            hit.get("assertion_id")
            and any(
                evidence.get("source_ref") == "demo://archive-hours"
                and evidence.get("dataset_id") == "docs-dataset"
                and evidence.get("chunk_id") == "docs-chunk-1"
                for evidence in hit.get("provenance", [])
            )
            for hit in result["results"]
        ):
            raise RuntimeError("No source-linked scoped evidence returned")
        for hit in result["results"]:
            print("Assertion:", hit["assertion_id"])
            for evidence in hit.get("provenance", []):
                print(
                    "Source:", evidence["source_ref"],
                    "dataset:", evidence["dataset_id"],
                    "chunk:", evidence["chunk_id"],
                )
        client.end_session(session_id=session["session_id"])
        print("COMMITTED; scoped evidence found")


if __name__ == "__main__":
    main()
