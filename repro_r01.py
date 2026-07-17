import asyncio
import os
import shutil

from mesa_storage.vector_engine import VectorEngine


async def main() -> None:
    uri = "./storage/vectors_test.lance"
    if os.path.exists(uri):
        shutil.rmtree(uri)

    engine = VectorEngine(uri)
    await engine.initialize()

    # Insert a vector for agent_A
    node_id = "test-node-123"
    await engine.upsert(node_id, "agent_A", [0.1, 0.2, 0.3])

    # Prove it exists
    res = await engine.search([0.1, 0.2, 0.3], agent_id="agent_A")
    print("Before delete:", len(res))

    # Bug: We can delete without providing agent_id
    try:
        await engine.soft_delete(node_id, "agent_wrong")
        print("Bug still exists: allowed to delete with wrong agent.")
    except Exception:
        print("Prevented delete with wrong agent")

    await engine.soft_delete(node_id, "agent_A")

    res_after = await engine.search([0.1, 0.2, 0.3], agent_id="agent_A")
    print("After delete:", len(res_after))


if __name__ == "__main__":
    asyncio.run(main())
