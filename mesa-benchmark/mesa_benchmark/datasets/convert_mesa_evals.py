import json
import os
import sys
from pathlib import Path

# Add project root to path so we can import mesa_evals
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from mesa_evals.generator import generate_synthetic_entries


def convert() -> None:
    entries = generate_synthetic_entries()
    scenarios = []

    for i, e in enumerate(entries):
        contexts = []
        context_ids = []
        for j, frag in enumerate(e["context_fragments"]):
            c_id = f"ctx_{e['id']}_{j}"
            context_ids.append(c_id)
            contexts.append(
                {
                    "id": c_id,
                    "text": frag,
                    "metadata": {"domain": e.get("domain", "general")},
                }
            )

        q = {
            "id": e["id"],
            "query": e["query"],
            "ground_truth": e["ground_truth_answer"],
            "expected_context_ids": context_ids,
            "evaluation_strategy": "llm_judge",
        }

        scenarios.append(
            {
                "id": f"scen_{e['id']}",
                "name": f"Synthetic {e.get('domain', 'Gen')} Scenario {i}",
                "description": f"Generated scenario (Tier {e.get('metadata', {}).get('complexity_tier', 1)})",
                "contexts": contexts,
                "questions": [q],
            }
        )

    out_path = Path(__file__).parent / "synthetic_dataset.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)
    print(f"Written {len(scenarios)} scenarios to {out_path}")


if __name__ == "__main__":
    convert()
