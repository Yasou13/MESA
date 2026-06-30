import asyncio
import logging
import os

from dotenv import load_dotenv

# Load env variables including GEMINI_API_KEY
load_dotenv()

from benchmarks.phase2_ablation.generators.procedural_gen import (  # noqa: E402
    ProceduralDatasetGenerator,
)

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    if not os.environ.get("LLM_API_KEY"):
        raise ValueError("LLM_API_KEY is not set in the environment or .env file.")

    os.environ["GROQ_API_KEY"] = os.environ["LLM_API_KEY"]

    generator = ProceduralDatasetGenerator(model="groq/llama-3.1-8b-instant")
    output_path = "benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"

    print("Starting procedural generation of 100 adversarial scenarios...")
    await generator.generate_dataset(100, output_path)
    print(f"Generation complete. Data saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
