import asyncio

import aiohttp


async def main() -> None:
    headers = {
        "X-API-Key": "mesa_prod_sec_2026_xyz",
        "Content-Type": "application/json",
    }
    payload = {"agent_id": "a", "session_id": "b", "content": "c"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:8000/v3/memory/insert", json=payload, headers=headers
        ) as resp:
            print(resp.status)
            print(await resp.text())


asyncio.run(main())
