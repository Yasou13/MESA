import logging
import os
import sys

# Add mesa_benchmark to path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../mesa-benchmark"))
)

from mesa_benchmark.clients.mesa_client import MesaClientAdapter
from mesa_benchmark.datasets.schemas import BenchmarkQuestion

logging.basicConfig(level=logging.INFO)


def main():
    print("Initializing MesaClientAdapter...")
    client = MesaClientAdapter()
    client.initialize({})
    print("MesaClientAdapter initialized.")

    q0 = BenchmarkQuestion(
        id="15_instruction_following_q0",
        query="What features I should pay attnetion to in sneakers?",
        ground_truth="",
        evaluation_strategy="llm_judge",
        metadata={},
    )

    print("Testing q0...")
    resp0 = client.answer(q0)
    print("q0 response latency:", resp0.latency_ms)

    q1 = BenchmarkQuestion(
        id="15_instruction_following_q1",
        query="What materials are commonly used in making modern sneakers, and what should I know about their overall quality?",
        ground_truth="",
        evaluation_strategy="llm_judge",
        metadata={},
    )

    print("Testing q1...")
    resp1 = client.answer(q1)
    print("q1 response latency:", resp1.latency_ms)

    print("Test finished successfully without deadlock.")


if __name__ == "__main__":
    main()
