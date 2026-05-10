import sys
import re

file_path = "/home/yasin/Desktop/MESA/mesa_memory/consolidation/loop.py"

with open(file_path, "r") as f:
    content = f.read()

tier3_method = """    async def _tier3_validate(self, record: dict) -> bool:
        content = record.get("content_payload", "")
        source = record.get("source", "")
        performative = record.get("performative", "")
        prompt_a = VALENCE_PROMPT_A_TEMPLATE.format(content=content, source=source, performative=performative)
        prompt_b = VALENCE_PROMPT_B_TEMPLATE.format(content=content, source=source, performative=performative)
        
        loop = asyncio.get_running_loop()
        try:
            raw_a = await loop.run_in_executor(None, self.llm_a.complete, prompt_a)
            cleaned_a = _strip_markdown_json(raw_a) if isinstance(raw_a, str) else ""
            result_a = json.loads(cleaned_a) if cleaned_a else raw_a
            decision_a = result_a.get("decision", "DISCARD")
        except Exception:
            decision_a = "DISCARD"
            
        try:
            raw_b = await loop.run_in_executor(None, self.llm_b.complete, prompt_b)
            cleaned_b = _strip_markdown_json(raw_b) if isinstance(raw_b, str) else ""
            result_b = json.loads(cleaned_b) if cleaned_b else raw_b
            decision_b = result_b.get("decision", "DISCARD")
        except Exception:
            decision_b = "DISCARD"
            
        if decision_a == "STORE" and decision_b == "STORE":
            return True
        elif decision_a == "DISCARD" and decision_b == "DISCARD":
            return False
            
        latency = record.get("resource_cost_latency_ms", 0.0)
        return latency <= config.tiebreaker_latency_threshold_ms

    async def run_batch(self, batch: list[dict] = None):"""

content = content.replace("    async def run_batch(self, batch: list[dict] = None):", tier3_method)

run_batch_logic = """
        if not batch:
            return

        ready_batch = []
        for record in batch:
            if record.get("tier3_deferred"):
                is_valid = await self._tier3_validate(record)
                if is_valid:
                    self.obs_layer.log_valence_decision(
                        tier=3, decision="ADMIT",
                        justification="Deferred Tier-3 validation passed in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    ready_batch.append(record)
                else:
                    self.obs_layer.log_valence_decision(
                        tier=3, decision="DISCARD",
                        justification="Deferred Tier-3 validation failed in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    await self.storage.raw_log.soft_delete(record.get("cmb_id", ""))
            else:
                ready_batch.append(record)
        
        batch = ready_batch
        if not batch:
            return

        start_ms = time.time() * 1000"""

content = content.replace("""        if not batch:
            return

        start_ms = time.time() * 1000""", run_batch_logic)


with open(file_path, "w") as f:
    f.write(content)

print("Updated loop.py")
