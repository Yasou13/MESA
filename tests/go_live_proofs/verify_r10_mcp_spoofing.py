import asyncio
import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


from mesa_mcp.server import call_tool


async def main():
    import mesa_mcp.server

    mesa_mcp.server.MESA_AGENT_ID = "secure_system_agent"

    # Simulate an LLM hallucinating/injecting a different agent_id
    args = {
        "session_id": "test_session",
        "content": "Malicious payload",
        "agent_id": "hacker_agent",
    }

    # We mock AsyncMesaClient to see what it received, because we just want to verify it uses the env var.
    # Wait, the best way to verify is to see what the server uses. The server code directly reads MESA_AGENT_ID.
    # So we can just check the code path or mock the client.

    # Let's mock AsyncMesaClient.insert to capture the request
    captured_request = None

    from mesa_client.client import AsyncMesaClient

    original_insert = AsyncMesaClient.insert

    async def mock_insert(self, request):
        nonlocal captured_request
        captured_request = request

        class MockResp:
            status = "STORED"
            node_id = "mock_123"
            log_id = 123

        return MockResp()

    AsyncMesaClient.insert = mock_insert

    try:
        print("Invoking MCP tool with injected agent_id='hacker_agent'...")
        result = await call_tool("record_memory", args)
        print("Tool result:", result)

        assert captured_request is not None, "Tool didn't call insert"
        print(f"Server attempted insert with agent_id: {captured_request.agent_id}")

        assert (
            captured_request.agent_id == "secure_system_agent"
        ), "VULNERABILITY: MCP Server accepted spoofed agent_id!"
        print(
            "SUCCESS: MCP Server safely ignored the injected agent_id (R-10 is FIXED)."
        )

    finally:
        AsyncMesaClient.insert = original_insert


if __name__ == "__main__":
    asyncio.run(main())
