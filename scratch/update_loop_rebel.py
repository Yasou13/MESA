import sys

file_path = "/home/yasin/Desktop/MESA/mesa_memory/consolidation/loop.py"

with open(file_path, "r") as f:
    content = f.read()

import_statement = """from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.extraction.rebel_pipeline import RebelExtractor
"""
content = content.replace(
    "from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet\nfrom mesa_memory.observability.metrics import ObservabilityLayer",
    import_statement
)

init_method_original = """    def __init__(
        self,
        storage_facade: StorageFacade,
        embedder: BaseUniversalLLMAdapter,
        llm_a: BaseUniversalLLMAdapter,
        llm_b: BaseUniversalLLMAdapter,
        obs_layer: ObservabilityLayer,
    ):
        self.storage = storage_facade
        self.embedder = embedder
        self.llm_a = llm_a
        self.llm_b = llm_b
        self.obs_layer = obs_layer
        self._running = False
        self.human_review_queue = deque(maxlen=config.human_review_max_size)
        self.dead_letter_queue = deque(maxlen=config.human_review_max_size)"""

init_method_new = """    def __init__(
        self,
        storage_facade: StorageFacade,
        embedder: BaseUniversalLLMAdapter,
        llm_a: BaseUniversalLLMAdapter,
        llm_b: BaseUniversalLLMAdapter,
        obs_layer: ObservabilityLayer,
    ):
        self.storage = storage_facade
        self.embedder = embedder
        self.llm_a = llm_a
        self.llm_b = llm_b
        self.obs_layer = obs_layer
        self._running = False
        self.human_review_queue = deque(maxlen=config.human_review_max_size)
        self.dead_letter_queue = deque(maxlen=config.human_review_max_size)
        self.rebel_extractor = RebelExtractor()"""

content = content.replace(init_method_original, init_method_new)

# Now inject rebel logic in `run_batch`
# Let's find where run_batch does the parallel dual-LLM calls

parallel_calls_original = """        # Parallel dual-LLM calls via existing adapter contract
        raw_a, raw_b = await asyncio.gather(
            loop.run_in_executor(
                None,
                functools.partial(
                    self.llm_a.complete, prompt_a, BatchExtractionResponse,
                ),
            ),
            loop.run_in_executor(
                None,
                functools.partial(
                    self.llm_b.complete, prompt_b, BatchExtractionResponse,
                ),
            ),
        )

        # --- Parse LLM_A response with recovery pipeline ---
        try:
            response_a = self._parse_batch_response(raw_a, len(sorted_batch))
            indexed_a, missing_a = self._audit_batch_coverage(
                response_a, len(sorted_batch),
            )
        except ValueError:
            logger.warning(
                f"Batch {batch_id}: LLM_A total parse failure, "
                f"entering bisection for {len(sorted_batch)} records"
            )
            indexed_a = await self._retry_with_bisection(
                sorted_batch, BATCH_PROMPT_A_TEMPLATE,
                PROMPT_A_TEMPLATE, self.llm_a,
            )
            missing_a = []

        # --- Parse LLM_B response with recovery pipeline ---
        try:
            response_b = self._parse_batch_response(raw_b, len(sorted_batch))
            indexed_b, missing_b = self._audit_batch_coverage(
                response_b, len(sorted_batch),
            )
        except ValueError:
            logger.warning(
                f"Batch {batch_id}: LLM_B total parse failure, "
                f"entering bisection for {len(sorted_batch)} records"
            )
            indexed_b = await self._retry_with_bisection(
                sorted_batch, BATCH_PROMPT_B_TEMPLATE,
                PROMPT_B_TEMPLATE, self.llm_b,
            )
            missing_b = []

        # --- Backfill missing indices via 1:1 fallback ---
        for idx in missing_a:
            trip = await self._single_record_extract(
                sorted_batch[idx], self.llm_a, PROMPT_A_TEMPLATE,
            )
            if trip and trip.get("head"):
                indexed_a[idx] = ExtractedTriplet(
                    record_index=idx,
                    head=trip["head"],
                    relation=trip.get("relation", ""),
                    tail=trip.get("tail", ""),
                )

        for idx in missing_b:
            trip = await self._single_record_extract(
                sorted_batch[idx], self.llm_b, PROMPT_B_TEMPLATE,
            )
            if trip and trip.get("head"):
                indexed_b[idx] = ExtractedTriplet(
                    record_index=idx,
                    head=trip["head"],
                    relation=trip.get("relation", ""),
                    tail=trip.get("tail", ""),
                )"""

parallel_calls_new = """        indexed_a = {}
        indexed_b = {}
        missing_a = list(range(len(sorted_batch)))
        missing_b = list(range(len(sorted_batch)))

        # Zero-Cost Pipeline (REBEL)
        for idx, record in enumerate(sorted_batch):
            try:
                # Run the synchronous pipeline in an executor to avoid blocking the event loop
                triplets = await loop.run_in_executor(
                    None, self.rebel_extractor.extract_triplets, record.get("content_payload", "")
                )
                if triplets:
                    # REBEL acts as both A and B for deterministic extraction
                    indexed_a[idx] = ExtractedTriplet(
                        record_index=idx,
                        head=triplets[0]["head"],
                        relation=triplets[0]["relation"],
                        tail=triplets[0]["tail"],
                    )
                    indexed_b[idx] = ExtractedTriplet(
                        record_index=idx,
                        head=triplets[0]["head"],
                        relation=triplets[0]["relation"],
                        tail=triplets[0]["tail"],
                    )
                    missing_a.remove(idx)
                    missing_b.remove(idx)
            except Exception as e:
                logger.warning(f"REBEL extraction failed for record {idx}: {e}")

        # --- Fallback to Expensive LLM for missing records ---
        if missing_a:
            fallback_batch = [sorted_batch[i] for i in missing_a]
            logger.info(f"Batch {batch_id}: Falling back to LLMs for {len(fallback_batch)} records.")
            
            fallback_records_block = self._build_records_block(fallback_batch)
            fb_prompt_a = BATCH_PROMPT_A_TEMPLATE.format(records_block=fallback_records_block)
            fb_prompt_b = BATCH_PROMPT_B_TEMPLATE.format(records_block=fallback_records_block)

            raw_a, raw_b = await asyncio.gather(
                loop.run_in_executor(None, functools.partial(self.llm_a.complete, fb_prompt_a, BatchExtractionResponse)),
                loop.run_in_executor(None, functools.partial(self.llm_b.complete, fb_prompt_b, BatchExtractionResponse)),
            )

            try:
                response_a = self._parse_batch_response(raw_a, len(fallback_batch))
                fb_indexed_a, fb_missing_a = self._audit_batch_coverage(response_a, len(fallback_batch))
            except ValueError:
                fb_indexed_a = await self._retry_with_bisection(fallback_batch, BATCH_PROMPT_A_TEMPLATE, PROMPT_A_TEMPLATE, self.llm_a)
                fb_missing_a = []

            try:
                response_b = self._parse_batch_response(raw_b, len(fallback_batch))
                fb_indexed_b, fb_missing_b = self._audit_batch_coverage(response_b, len(fallback_batch))
            except ValueError:
                fb_indexed_b = await self._retry_with_bisection(fallback_batch, BATCH_PROMPT_B_TEMPLATE, PROMPT_B_TEMPLATE, self.llm_b)
                fb_missing_b = []

            # Map fallback results back to global indices
            for local_idx, triplet in fb_indexed_a.items():
                global_idx = missing_a[local_idx]
                triplet.record_index = global_idx
                indexed_a[global_idx] = triplet

            for local_idx, triplet in fb_indexed_b.items():
                global_idx = missing_b[local_idx]
                triplet.record_index = global_idx
                indexed_b[global_idx] = triplet

            for local_idx in fb_missing_a:
                global_idx = missing_a[local_idx]
                trip = await self._single_record_extract(sorted_batch[global_idx], self.llm_a, PROMPT_A_TEMPLATE)
                if trip and trip.get("head"):
                    indexed_a[global_idx] = ExtractedTriplet(record_index=global_idx, head=trip["head"], relation=trip.get("relation", ""), tail=trip.get("tail", ""))

            for local_idx in fb_missing_b:
                global_idx = missing_b[local_idx]
                trip = await self._single_record_extract(sorted_batch[global_idx], self.llm_b, PROMPT_B_TEMPLATE)
                if trip and trip.get("head"):
                    indexed_b[global_idx] = ExtractedTriplet(record_index=global_idx, head=trip["head"], relation=trip.get("relation", ""), tail=trip.get("tail", ""))"""

content = content.replace(parallel_calls_original, parallel_calls_new)

with open(file_path, "w") as f:
    f.write(content)

print("Updated loop.py with REBEL fallback logic")
