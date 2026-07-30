"""
Query Decomposition for MESA Hybrid Retrieval.

Decomposes complex multi-hop queries into a list of simpler, focused subqueries
using an LLM. This addresses the similarity dilution problem in multi-hop questions.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("MESA_Decomposition")


class SubqueryList(BaseModel):
    subqueries: list[str] = Field(description="A list of 1 to 5 focused subqueries.")


async def decompose_query(query: str, llm_adapter: Any) -> list[str]:
    """
    Decompose the user's question into 1 to 5 focused retrieval subqueries.

    Args:
        query: The complex user question.
        llm_adapter: An LLM adapter instance capable of structured output (`acomplete`).

    Returns:
        A list of string subqueries. If decomposition fails or the adapter
        does not support generation, falls back to returning the original query.
    """
    if llm_adapter is None:
        return [query]

    prompt = f"""
Decompose the user's question into 1 to 5 focused retrieval subqueries.
Rules:
- Preserve the original order of reasoning from broadest to most specific
- Keep each subquery concise and self-contained
- Do not answer the question

Question: {query}
"""
    try:
        # Assuming llm_adapter supports `acomplete` with a pydantic schema
        response = await llm_adapter.acomplete(prompt, schema=SubqueryList)

        if hasattr(response, "subqueries"):
            subqueries = response.subqueries
        elif isinstance(response, dict) and "subqueries" in response:
            subqueries = response["subqueries"]
        else:
            subqueries = [query]

        if not subqueries:
            return [query]

        logger.debug(
            "DECOMPOSED_QUERY | query_length=%d subquery_count=%d",
            len(query),
            len(subqueries),
        )
        return subqueries  # type: ignore[str]

    except Exception as exc:
        logger.warning(
            "QUERY_DECOMPOSITION_FAILED | query_length=%d exception_type=%s",
            len(query),
            type(exc).__name__,
        )
        return [query]
