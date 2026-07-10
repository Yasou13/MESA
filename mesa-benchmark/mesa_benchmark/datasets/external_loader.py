"""
External Benchmark Loader Adapter.
Converts external standard datasets (such as LoCoMo or LongMemEval formats)
into standard MESA BenchmarkScenario objects.
"""

import json
from pathlib import Path
from typing import List, Union

from .schemas import BenchmarkQuestion, BenchmarkScenario, MemoryContext


class ExternalDatasetLoader:
    """Adapter for converting external QA/Memory datasets to MESA BenchmarkScenarios."""

    @staticmethod
    def load_locomo_format(file_path: Union[str, Path]) -> List[BenchmarkScenario]:
        """
        Loads a LoCoMo-style dialogue memory benchmark dataset and transforms it
        into a list of BenchmarkScenario instances.

        LoCoMo typically provides conversation turns and multi-hop questions
        referencing specific memory facts across sessions.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"External dataset not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        scenarios: List[BenchmarkScenario] = []
        items = raw_data if isinstance(raw_data, list) else raw_data.get("data", [])

        for idx, item in enumerate(items):
            scenario_id = str(item.get("id", f"locomo_{idx}"))
            name = item.get("title", f"LoCoMo Scenario {idx}")
            desc = item.get("category", "multi_hop_dialogue")

            # Parse contexts / memory turns
            contexts: List[MemoryContext] = []
            raw_contexts = item.get("context", item.get("conversation", []))
            for c_idx, turn in enumerate(raw_contexts):
                if isinstance(turn, dict):
                    turn_id = str(turn.get("id", f"ctx_{c_idx}"))
                    text = turn.get("text", turn.get("content", ""))
                    metadata = turn.get("metadata", {})
                else:
                    turn_id = f"ctx_{c_idx}"
                    text = str(turn)
                    metadata = {}

                if text.strip():
                    contexts.append(
                        MemoryContext(id=turn_id, text=text, metadata=metadata)
                    )

            # Parse questions
            questions: List[BenchmarkQuestion] = []
            raw_questions = item.get("qa_pairs", item.get("questions", []))
            for q_idx, qa in enumerate(raw_questions):
                q_id = str(qa.get("id", f"q_{idx}_{q_idx}"))
                query = qa.get("question", "")
                ground_truth = qa.get("answer", "")
                expected_ids = qa.get(
                    "supporting_facts", qa.get("expected_context_ids", [])
                )
                strategy = qa.get("evaluation_strategy", "llm_judge")

                if query.strip():
                    questions.append(
                        BenchmarkQuestion(
                            id=q_id,
                            query=query,
                            ground_truth=ground_truth,
                            expected_context_ids=expected_ids,
                            evaluation_strategy=strategy,
                        )
                    )

            if contexts and questions:
                scenarios.append(
                    BenchmarkScenario(
                        id=scenario_id,
                        name=name,
                        description=desc,
                        contexts=contexts,
                        questions=questions,
                    )
                )

        return scenarios
