import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

# Ensure mesa_benchmark is importable
sys.path.insert(0, os.path.abspath("mesa-benchmark"))

from mesa_benchmark.clients.mesa_client import MesaClientAdapter
from mesa_benchmark.datasets.schemas import BenchmarkQuestion


def verify_deadlock():
    os.environ["HF_HUB_OFFLINE"] = "1"

    adapter = MesaClientAdapter()
    adapter.initialize(
        {
            "storage": {"type": "mesa"},
            "vector_dims": 8,
            "enable_multi_hop": True,
            "enable_rerank": False,
        }
    )

    logging.info(
        "Adapter initialized. Running specifically for known deadlock questions."
    )

    q0 = BenchmarkQuestion(
        id="15_instruction_following_q0",
        query="Explain the concept of strict liability under Turkish Obligations Law.",
        ground_truth_context_ids=["dummy1", "dummy2"],
        ground_truth="Strict liability means liability without fault.",
    )

    q1 = BenchmarkQuestion(
        id="15_instruction_following_q1",
        query="What are the exceptions to strict liability?",
        ground_truth_context_ids=["dummy3"],
        ground_truth="Force majeure and gross negligence of the victim.",
    )

    try:
        logging.info("Testing q0...")
        resp0 = adapter.answer(q0)
        logging.info(
            f"q0 finished successfully: {resp0.latency_ms}ms, context_len: {len(resp0.retrieved_context_ids)}"
        )

        logging.info("Testing q1...")
        resp1 = adapter.answer(q1)
        logging.info(
            f"q1 finished successfully: {resp1.latency_ms}ms, context_len: {len(resp1.retrieved_context_ids)}"
        )

        logging.info("SUCCESS: Deadlock questions executed without hanging!")
        return 0
    except Exception as e:
        logging.error(f"Failed to execute questions: {e}")
        return 1
    finally:
        adapter.close()


if __name__ == "__main__":
    sys.exit(verify_deadlock())
