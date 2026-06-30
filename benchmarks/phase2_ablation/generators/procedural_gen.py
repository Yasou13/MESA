import asyncio
import json
import logging
import os
import random
import uuid

import litellm
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("MESA_ProceduralGen")


class AdversarialScenario(BaseModel):
    id: str = Field(description="a unique string identifier")
    domain: str
    circuit_type: str
    context_t0: str = Field(description="The older document establishing fact A.")
    context_t1: str = Field(
        description="The newer document establishing fact B (contradicts A)."
    )
    question: str = Field(
        description="A specific question asking about the current state of the fact."
    )
    ground_truth_answer: str = Field(
        description="The correct answer based on context_t1."
    )
    target_entity: str = Field(
        description="The specific entity/concept that has conflicting facts."
    )


class ProceduralDatasetGenerator:
    """Dynamic scenario generator using UniversalProvider (LiteLLM)

    Generates two types of scenarios for balanced evaluation:
    - t1_valid: Genuine temporal overrides where t1 supersedes t0
    - t0_valid: Red herrings where t1 describes a DIFFERENT entity/context,
      so t0 remains the valid answer (tests distractor resilience)
    """

    def __init__(self, model: str = "groq/llama-3.1-8b-instant"):
        self.model = model
        self._override_prompt = (
            "You are an expert scenario generator for testing advanced RAG systems. "
            "Generate a highly camouflaged epistemic conflict scenario. "
            "The conflict must be resolvable ONLY by applying strict temporal precedence (the newer information overrides the older). "
            "Output STRICTLY as a JSON object adhering to the specified schema exactly. Do not output anything else."
        )
        self._red_herring_prompt = (
            "You are an expert scenario generator for testing advanced RAG systems. "
            "Generate a RED HERRING scenario where two documents appear related but concern DIFFERENT entities. "
            "Document t0 establishes a fact about Entity A. Document t1 establishes a DIFFERENT fact about Entity B "
            "(a distinct entity in the same domain). The question asks specifically about Entity A, so the answer "
            "should come from t0 ONLY. t1 is a distractor. "
            "Output STRICTLY as a JSON object adhering to the specified schema exactly. Do not output anything else."
        )

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        reraise=True,
    )
    async def _generate_single(
        self, domain: str, circuit_type: str, is_red_herring: bool = False
    ) -> AdversarialScenario:
        system_prompt = (
            self._red_herring_prompt if is_red_herring else self._override_prompt
        )

        if is_red_herring:
            user_prompt = (
                f"Domain: {domain}\n"
                f"Circuit Type: {circuit_type}\n"
                "Generate a RED HERRING scenario: t0 describes Entity A, t1 describes Entity B (different entity). "
                "The question asks about Entity A specifically, so the correct answer is from t0. "
                "Make it heavily camouflaged (e.g. corporate memos, legal briefs, medical records). "
                "Output strictly as a JSON object with exactly these keys. All values MUST be pure flat strings:\n"
                "{\n"
                '  "context_t0": "Document about Entity A establishing fact.",\n'
                '  "context_t1": "Document about Entity B (different entity, same domain).",\n'
                '  "question": "A question specifically about Entity A.",\n'
                '  "ground_truth_answer": "The answer from t0 about Entity A.",\n'
                '  "target_entity": "Entity A name"\n'
                "}"
            )
        else:
            user_prompt = (
                f"Domain: {domain}\n"
                f"Circuit Type: {circuit_type}\n"
                "Ensure the scenario is heavily camouflaged (e.g. corporate memos, legal briefs, medical records). "
                "Output strictly as a JSON object with exactly these keys. All values MUST be pure flat strings, DO NOT use nested objects/dictionaries:\n"
                "{\n"
                '  "context_t0": "The older document establishing fact A.",\n'
                '  "context_t1": "The newer document establishing fact B (contradicts A).",\n'
                '  "question": "A specific question asking about the current state of the fact.",\n'
                '  "ground_truth_answer": "The correct answer based on context_t1.",\n'
                '  "target_entity": "The specific entity/concept that has conflicting facts."\n'
                "}"
            )

        response = await litellm.acompletion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        try:
            # Clean JSON if there are markdown tags
            if "```json" in content:
                clean = content.split("```json")[1].split("```")[0].strip()
            else:
                clean = content

            data = json.loads(clean)

            # Coerce any nested dicts to string just in case the model disobeys
            for key in [
                "context_t0",
                "context_t1",
                "question",
                "ground_truth_answer",
                "target_entity",
            ]:
                if key in data and isinstance(data[key], dict):
                    data[key] = json.dumps(data[key])

            # Override ID to ensure consistency
            data["id"] = f"scen_{uuid.uuid4().hex[:8]}"
            data["domain"] = domain
            data["circuit_type"] = circuit_type

            # This will raise ValidationError if schema is wrong, which Tenacity will catch and retry
            scenario = AdversarialScenario(**data)

            logger.info(
                f"Successfully generated scenario for {domain} - {circuit_type}"
            )
            return scenario

        except Exception as e:
            logger.error(
                f"Failed to generate or validate output. Retrying... Error: {e}"
            )
            raise

    async def generate_dataset(self, n: int, output_path: str) -> None:
        domains = [
            "Legal",
            "Medical",
            "Financial",
            "Technology",
            "Insurance",
            "Real Estate",
        ]
        circuit_types = ["parallel", "series", "epistemic_override"]

        tasks = []
        semaphore = asyncio.Semaphore(1)

        async def bounded_generate(d: str, c: str, is_rh: bool) -> AdversarialScenario:
            async with semaphore:
                # Add a small delay between requests to help with rate limits
                await asyncio.sleep(2)
                return await self._generate_single(d, c, is_red_herring=is_rh)

        # Balanced generation: 50% t1_valid (temporal override), 50% t0_valid (red herring)
        n_override = n // 2
        n_red_herring = n - n_override

        for _ in range(n_override):
            domain = random.choice(domains)
            circuit = random.choice(circuit_types)
            tasks.append(bounded_generate(domain, circuit, False))

        for _ in range(n_red_herring):
            domain = random.choice(domains)
            circuit = random.choice(circuit_types)
            tasks.append(bounded_generate(domain, circuit, True))

        # Shuffle to interleave types
        random.shuffle(tasks)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_scenarios: list[AdversarialScenario] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Generation failed: {r}")
                raise r
            elif isinstance(r, AdversarialScenario):
                valid_scenarios.append(r)

        with open(output_path, "w", encoding="utf-8") as f:
            for s in valid_scenarios:
                f.write(s.model_dump_json() + "\n")

        logger.info(
            f"Successfully generated {len(valid_scenarios)} scenarios to {output_path}"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("No valid API provider configured")

    generator = ProceduralDatasetGenerator()
    asyncio.run(
        generator.generate_dataset(
            100, "benchmarks/phase2_ablation/data/synthetic_dataset.jsonl"
        )
    )
