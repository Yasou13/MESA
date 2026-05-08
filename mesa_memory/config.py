import psutil
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class MesaConfig(BaseSettings):
    model_config = ConfigDict(env_prefix="MESA_")

    tiebreaker_latency_threshold_ms: float = 500.0
    bootstrap_cosine_threshold: float = 0.75
    context_window_limit: int = 8000
    lancedb_memory_limit_bytes: int = 3 * 1024 * 1024 * 1024
    spacy_language_model: str = "xx_ent_wiki_sm"

    # Cross-validation lock thresholds (Module 8)
    entity_similarity_threshold: float = 0.80
    relation_similarity_threshold: float = 0.70

    # Consolidation loop parameters (Module 8)
    consolidation_batch_size: int = 20
    hub_degree_threshold: int = 5
    consolidation_idle_timeout: int = 10
    uncertain_zone_lower_bound: float = 0.3

    # P0-A: Batch processing & token compression
    batch_llm_chunk_size: int = 8            # MESA_BATCH_LLM_CHUNK_SIZE
    max_batch_tokens: int = 6000             # MESA_MAX_BATCH_TOKENS
    truncation_max_retries: int = 2          # MESA_TRUNCATION_MAX_RETRIES

    # Valence recalibration interval (Module 7)
    recalibration_interval: int = 50


def calculate_dynamic_limits(config: MesaConfig) -> MesaConfig:
    total_ram = psutil.virtual_memory().total
    config.lancedb_memory_limit_bytes = int(total_ram * 0.18)
    return config


config = calculate_dynamic_limits(MesaConfig())
