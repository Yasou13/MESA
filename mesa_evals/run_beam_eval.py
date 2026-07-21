#!/usr/bin/env python3
"""
BEAM Benchmark Evaluation Runner for MESA.

Runs the BEAM Benchmark (Beyond a Million Tokens) against a specified
MESA BaseMemoryClient adapter. It tests multi-session continuity and
long-term memory recall over massive context sizes.

Usage:
    python -m mesa_evals.run_beam_eval --adapter mesa --dataset mesa-benchmark/datasets/beam/dataset.json
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from mesa_evals.benchmark_adapters.factory import get_adapter
from mesa_memory.adapter.factory import AdapterFactory

logger = logging.getLogger("MESA_BEAM_EVAL")


async def evaluate_beam(
    adapter_name: str,
    dataset_path: Path,
    limit: int = 0,
    agent_id: str = "beam_eval_agent",
    concurrency: int = 5,
) -> None:
    logger.info("Initializing %s adapter for BEAM evaluation...", adapter_name)
    client = get_adapter(adapter_name)
    await client.initialize()
    await client.clear_memory(agent_id=agent_id)

    llm_judge = AdapterFactory.get_adapter("auto")

    with open(dataset_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    if limit > 0:
        scenarios = scenarios[:limit]
        logger.info("Limiting evaluation to %d scenarios.", limit)

    total_scenarios = len(scenarios)
    total_questions = sum(len(s["questions"]) for s in scenarios)

    logger.info(
        "Loaded %d scenarios with %d probing questions.",
        total_scenarios,
        total_questions,
    )

    report: dict[str, Any] = {
        "adapter": adapter_name,
        "dataset": str(dataset_path),
        "scenarios": total_scenarios,
        "total_questions": total_questions,
        "results": [],
        "metrics": {
            "total_hits": 0,
            "total_queries": 0,
            "accuracy": 0.0,
        },
    }

    semaphore = asyncio.Semaphore(concurrency)

    async def _ingest_turn(turn_text: str):  # type: ignore[no-untyped-def]
        async with semaphore:
            await client.add_memory(
                content=turn_text,
                agent_id=agent_id,
                metadata={"source": "beam_benchmark"},
            )

    try:
        for s_idx, scenario in enumerate(scenarios):
            logger.info(
                "--- Processing Scenario %d/%d: %s ---",
                s_idx + 1,
                total_scenarios,
                scenario["name"],
            )

            # 1. Ingestion Phase
            contexts = scenario.get("contexts", [])
            logger.info("Ingesting %d conversation turns...", len(contexts))

            t0 = time.monotonic()

            # BEAM turns must be ingested sequentially to preserve temporal causality if the adapter relies on it.
            # But to speed up, we will batch if concurrency > 1, though sequential is safer for pure memory agents.
            # We will use sequential for accuracy, since BEAM evaluates temporal reasoning.
            for i, ctx in enumerate(contexts):
                await client.add_memory(
                    content=ctx["text"],
                    agent_id=agent_id,
                    metadata={"source": "beam_benchmark", "turn_id": ctx["id"]},
                )
                if (i + 1) % 50 == 0:
                    logger.info("  Ingested %d/%d turns...", i + 1, len(contexts))

            ingest_time = time.monotonic() - t0
            logger.info("Ingestion complete in %.2fs", ingest_time)

            # 2. Evaluation Phase
            questions = scenario.get("questions", [])
            logger.info("Evaluating %d probing questions...", len(questions))

            scenario_hits = 0
            scenario_queries = len(questions)

            for q_idx, q in enumerate(questions):
                query_text = q["query"]
                ideal = q["ground_truth"]

                t1 = time.monotonic()
                res = await client.query(query_text, agent_id=agent_id, limit=10)
                latency = time.monotonic() - t1

                if res.error:
                    logger.error("Query error on %s: %s", q["id"], res.error)
                    hit = False
                else:
                    # Truncate context to avoid TPM limits on free tier APIs
                    safe_context = (
                        res.context[:3000] + "... [TRUNCATED]"
                        if len(res.context) > 3000
                        else res.context
                    )
                    judge_prompt = f"""You are an objective evaluator grading a memory retrieval system.
Question: {query_text}
Expected Ideal Response: {ideal}
Actual Retrieved Context: {safe_context}

Does the actual retrieved context contain sufficient and accurate information to answer the question, aligning with the expected ideal response?
Answer strictly with YES or NO.
"""
                    judge_res = await llm_judge.acomplete(judge_prompt)
                    judge_text = str(judge_res).upper()
                    hit = "YES" in judge_text

                if hit:
                    scenario_hits += 1
                    report["metrics"]["total_hits"] += 1
                report["metrics"]["total_queries"] += 1

                q_result = {
                    "scenario_id": scenario["id"],
                    "question_id": q["id"],
                    "category": q.get("metadata", {}).get("category", "unknown"),
                    "hit": hit,
                    "latency_ms": latency * 1000,
                }
                report["results"].append(q_result)

                logger.info(
                    "  Q%d: hit=%s | category=%s | latency=%.0fms",
                    q_idx + 1,
                    hit,
                    q_result["category"],
                    q_result["latency_ms"],
                )

            logger.info(
                "Scenario %s accuracy: %.2f%%",
                scenario["name"],
                (scenario_hits / max(1, scenario_queries)) * 100,
            )

            # Clear memory for next scenario to avoid cross-contamination
            await client.clear_memory(agent_id=agent_id)

    except Exception as exc:
        logger.error("Evaluation aborted due to error: %s", exc, exc_info=True)
    finally:
        await client.shutdown()

        # Final Metrics
        hits = report["metrics"]["total_hits"]
        queries = report["metrics"]["total_queries"]
        accuracy = (hits / queries * 100) if queries > 0 else 0.0
        report["metrics"]["accuracy"] = accuracy

        logger.info("=== BEAM EVALUATION COMPLETE ===")
        logger.info("Adapter: %s", adapter_name)
        logger.info("Total Queries: %d", queries)
        logger.info("Total Hits: %d", hits)
        logger.info("Overall Accuracy: %.2f%%", accuracy)

        # Save report
        out_path = Path("beam_eval_report.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        logger.info("Report saved to %s", out_path.absolute())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Run BEAM Benchmark Evaluation.")
    parser.add_argument(
        "--adapter",
        type=str,
        default="mesa",
        choices=["mesa", "mem0", "barerag", "letta", "zep"],
        help="Adapter to evaluate (default: mesa)",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="Path to BEAM dataset JSON (e.g., mesa-benchmark/datasets/beam/dataset.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit the number of scenarios to evaluate (0 for all)",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default="beam_eval_agent",
        help="Agent ID to use for isolation (default: beam_eval_agent)",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        logger.error("Dataset not found: %s", dataset_path)
        sys.exit(1)

    try:
        asyncio.run(
            evaluate_beam(
                adapter_name=args.adapter,
                dataset_path=dataset_path,
                limit=args.limit,
                agent_id=args.agent_id,
            )
        )
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user.")


if __name__ == "__main__":
    main()
