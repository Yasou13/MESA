"""
Entity Description Consolidation worker for MESA self-healing graphs.

Periodically fetches all entity nodes, retrieves their 1-hop neighborhood,
and leverages the LLM adapter to consolidate everything known about the entity
into a unified description. This single rich description is then re-embedded
and saved back, drastically improving semantic search hit rates for complex queries.
"""

import asyncio
import logging
import time
from typing import Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Consolidation")


async def run_consolidation_scan(
    agent_id: str,
    dao: MemoryDAO,
    llm_adapter: BaseUniversalLLMAdapter,
) -> dict[str, Any]:
    t_start = time.monotonic()
    logger.info("CONSOLIDATION_SCAN_START | agent_id=%s", agent_id)

    # 1. Fetch all active entities
    entities = await dao.get_memories(agent_id, include_consolidated=True)

    if not entities:
        return {
            "agent_id": agent_id,
            "processed": 0,
            "elapsed_ms": (time.monotonic() - t_start) * 1000,
        }

    processed_count = 0

    for entity in entities:
        node_id = entity["id"]
        entity_name = entity.get("entity_name", "Unknown Entity")

        # 2. Get 1-hop neighbors
        neighbors = await dao.get_neighbors(
            agent_id=agent_id, node_id=node_id, max_hops=1, direction="both"
        )

        if not neighbors:
            continue

        # Format context from neighbors.
        # get_neighbors returns dicts with 'id', 'name', 'hops'.
        context_lines = []
        for n in neighbors:
            n_name = n.get("name", "Unknown")
            context_lines.append(f"- {n_name} (ENTITY)")

        if not context_lines:
            continue

        context_str = "\n".join(context_lines)

        # 3. Generate consolidated description
        prompt = (
            f"You are a knowledge graph assistant. Below are the known connections for the entity '{entity_name}'.\n"
            f"Connections:\n{context_str}\n\n"
            f"Write a single, comprehensive paragraph summarizing everything known about '{entity_name}' based on these connections."
        )

        try:
            consolidated_desc = await llm_adapter.acomplete(prompt)
            if not isinstance(consolidated_desc, str):
                consolidated_desc = str(consolidated_desc)

            # 4. Generate new embedding
            new_embedding = await llm_adapter.aembed(consolidated_desc)

            # 5. Update DAO
            await dao.update_entity_description(
                agent_id=agent_id,
                node_id=node_id,
                new_content=consolidated_desc,
                new_embedding=new_embedding,
            )
            processed_count += 1

        except Exception as exc:
            logger.warning(
                "CONSOLIDATION_FAILED | agent_id=%s node_id=%s error=%s",
                agent_id,
                node_id,
                exc,
            )

    elapsed_ms = (time.monotonic() - t_start) * 1000
    logger.info(
        "CONSOLIDATION_SCAN_DONE | agent_id=%s processed=%d elapsed_ms=%.1f",
        agent_id,
        processed_count,
        elapsed_ms,
    )

    return {
        "agent_id": agent_id,
        "processed": processed_count,
        "elapsed_ms": elapsed_ms,
    }


async def schedule_consolidation_worker(
    dao: MemoryDAO,
    llm_adapter: BaseUniversalLLMAdapter,
    interval_sec: int = 3600 * 4,  # e.g., every 4 hours
) -> None:
    logger.info("Consolidation worker scheduled (interval=%ds)", interval_sec)

    try:
        while True:
            try:
                agent_ids = await dao.get_all_active_agent_ids()
                for agent_id in agent_ids:
                    try:
                        await run_consolidation_scan(agent_id, dao, llm_adapter)
                    except Exception as exc:
                        logger.error(
                            "Consolidation scan failed for agent %s: %s", agent_id, exc
                        )
            except Exception as exc:
                logger.error("Consolidation worker encountered an error: %s", exc)

            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        logger.info("Consolidation worker cancelled.")
