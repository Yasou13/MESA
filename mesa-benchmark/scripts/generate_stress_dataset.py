import argparse
import json
import os
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "mesa_benchmark", "datasets", "stress_dataset.json")
)

# Templates for synthetic generation
COMPANIES = [
    "Acme Corp",
    "Ephesus Biotech",
    "Anatolian Technologies",
    "Global Dynamics",
    "Stark Industries",
    "Wayne Enterprises",
    "Cyberdyne Systems",
    "Tyrell Corp",
    "Omni Consumer Products",
    "Massive Dynamic",
    "Initech",
    "Umbrella Corp",
    "Aperture Science",
]

EVENTS = [
    "hired a new VP of Engineering",
    "opened a new office in London",
    "released version 2.0 of their flagship product",
    "announced a share buyback program",
    "faced a class-action lawsuit regarding data privacy",
    "acquired a smaller competitor",
    "experienced a minor security breach",
    "partnered with a leading university for AI research",
]


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def random_date(start_year=2023, end_year=2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    random_days = random.randint(0, (end - start).days)
    return (start + timedelta(days=random_days)).strftime("%Y-%m-%d")


def generate_financial_scenario(company: str, base_val: int) -> tuple[List[Dict], Dict]:
    scenario_id = str(uuid.uuid4())

    # Contexts
    date1 = "2024-03-01"
    val1 = base_val
    ctx1 = {
        "id": f"ctx_{scenario_id}_0",
        "text": f"In the Q1 2024 earnings call on {date1}, {company} management projected a net revenue of ${val1}M.",
        "metadata": {"domain": "financial"},
    }

    date2 = "2025-02-15"
    val2 = int(base_val * random.uniform(0.5, 1.5))
    ctx2 = {
        "id": f"ctx_{scenario_id}_1",
        "text": f"The audited FY2024 10-K filing released on {date2} for {company} reported an actual net revenue of ${val2}M.",
        "metadata": {"domain": "financial"},
    }

    date3 = "2024-06-10"
    ctx3 = {
        "id": f"ctx_{scenario_id}_2",
        "text": f"Sell-side analysts reiterated their Buy rating on {company} in {date3}, maintaining the ${val1}M revenue projection.",
        "metadata": {"domain": "financial"},
    }

    contexts = [ctx1, ctx2, ctx3]

    question = {
        "id": scenario_id,
        "query": f"Despite the Q1 projections, what was the actual audited net revenue reported by {company} for FY2024?",
        "ground_truth": f"The actual audited net revenue for FY2024 was ${val2}M, overriding the preliminary projection of ${val1}M.",
        "expected_context_ids": [ctx1["id"], ctx2["id"]],
        "evaluation_strategy": "llm_judge",
    }

    return contexts, question


def generate_noise_contexts(num_noise: int) -> List[Dict]:
    noise = []
    for _ in range(num_noise):
        company = random.choice(COMPANIES)
        event = random.choice(EVENTS)
        date = random_date()
        noise.append(
            {
                "id": generate_id("ctx"),
                "text": f"On {date}, {company} {event}.",
                "metadata": {"domain": "news", "is_noise": True},
            }
        )
    return noise


def generate_stress_dataset(
    num_scenarios: int, noise_per_scenario: int, output_path: str
):
    scenarios = []

    for i in range(num_scenarios):
        company = f"{random.choice(COMPANIES)} {i}"
        base_val = random.randint(10, 500)

        contexts, question = generate_financial_scenario(company, base_val)

        # Inject noise
        if noise_per_scenario > 0:
            noise = generate_noise_contexts(noise_per_scenario)
            contexts.extend(noise)
            # Shuffle contexts so the target isn't always at the beginning
            random.shuffle(contexts)

        scenario = {
            "id": f"scen_{question['id']}",
            "name": f"Stress Scenario {i}",
            "description": "Auto-generated financial temporal contradiction",
            "contexts": contexts,
            "questions": [question],
        }
        scenarios.append(scenario)

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)

    print(
        f"✅ Generated {num_scenarios} scenarios with {noise_per_scenario} noise contexts each."
    )
    print(f"💾 Total contexts: {num_scenarios * (3 + noise_per_scenario)}")
    print(f"📂 Saved to: {out_file.absolute()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate large-scale stress datasets for MESA Benchmark"
    )
    parser.add_argument(
        "--scenarios",
        type=int,
        default=100,
        help="Number of contradiction scenarios to generate",
    )
    parser.add_argument(
        "--noise",
        type=int,
        default=5,
        help="Number of random noise contexts to inject per scenario",
    )
    parser.add_argument(
        "--out", type=str, default=DEFAULT_OUT_PATH, help="Output JSON path"
    )

    args = parser.parse_args()

    print("🚀 MESA Benchmark Stress Dataset Generator")
    generate_stress_dataset(args.scenarios, args.noise, args.out)
