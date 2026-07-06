import asyncio
import logging
import os

from dotenv import load_dotenv

# Load env variables including GEMINI_API_KEY for the judge
load_dotenv()

from benchmarks.phase2_ablation.core.ablation_runner import (  # noqa: E402
    AblationRunner,
    MESAStateConfig,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
# Suppress noisy underlying framework logs
logging.getLogger("MESA_Storage").setLevel(logging.WARNING)


async def main() -> None:
    if not os.environ.get("LLM_API_KEY"):
        raise ValueError("LLM_API_KEY is not set in the environment or .env file.")

    os.environ["GROQ_API_KEY"] = os.environ["LLM_API_KEY"]

    dataset_path = "benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(
            f"{dataset_path} does not exist. Run generation script first."
        )

    runner = AblationRunner(dataset_path=dataset_path)

    configs = [
        MESAStateConfig("Naive_Vector_RAG", False, False, False),
        MESAStateConfig("Vector_Plus_Graph", True, False, False),
        MESAStateConfig("Vector_Plus_Consensus", False, True, False),
        MESAStateConfig("Full_MESA_Pipeline", True, True, True),
    ]

    print("==========================================================================")
    print("MESA PHASE 2 ABLATION DRY-RUN (10 SCENARIOS)")
    print("==========================================================================")

    results = []
    baseline = await runner._execute_workload(configs[0])
    results.append(baseline)

    for config in configs[1:]:
        variant = await runner._execute_workload(config)
        results.append(variant)

    print(
        "\n=========================================================================="
    )
    print("FINAL ABLATION MATRIX RESULTS")
    print("==========================================================================")
    print(
        f"{'Configuration':<25} | {'CRA (%)':<10} | {'TTFT (s)':<10} | {'Cost (USD)':<10}"
    )
    print("-" * 65)
    for res in results:
        print(
            f"{res.config_name:<25} | {res.accuracy_percent:<10.2f} | {res.avg_ttft_sec:<10.3f} | ${res.total_cost_usd:<9.6f}"
        )
    print("==========================================================================")


if __name__ == "__main__":
    asyncio.run(main())
