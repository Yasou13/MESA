import logging
import os
from typing import Optional

import psutil
from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=True)

logger = logging.getLogger("MESA_Config")

# Safe-mode fallback: 1 GB — intentionally restrictive to prevent OOM kills
# when no reliable memory reading is available.
_SAFE_MODE_RAM_BYTES = 1024 * 1024 * 1024

# Cgroup paths for containerised environments (Docker / Kubernetes)
_CGROUP_V1_PATH = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
_CGROUP_V2_PATH = "/sys/fs/cgroup/memory.max"

# cgroup "max" sentinel — kernels report this when no limit is set
_CGROUP_MAX_SENTINEL = "max"


# ---------------------------------------------------------------------------
# Hierarchical RAM detection helpers
# ---------------------------------------------------------------------------


def _read_env_ram_limit() -> Optional[int]:
    """Tier 2: Read ``MESA_MAX_RAM_MB`` environment variable.

    Returns total RAM in **bytes** or *None* if the variable is absent or
    contains a non-numeric value.
    """
    raw = os.environ.get("MESA_MAX_RAM_MB")
    if raw is None:
        return None
    try:
        mb = int(raw)
        if mb <= 0:
            logger.warning("MESA_MAX_RAM_MB=%s is non-positive; ignoring", raw)
            return None
        total = mb * 1024 * 1024
        logger.info(
            "RAM limit sourced from MESA_MAX_RAM_MB: %d MB (%d bytes)", mb, total
        )
        return total
    except ValueError:
        logger.warning("MESA_MAX_RAM_MB=%r is not a valid integer; ignoring", raw)
        return None


def _read_cgroup_ram_limit() -> Optional[int]:
    """Tier 3: Read Linux cgroup memory limit (v1 then v2).

    Returns total RAM in **bytes** or *None* if no cgroup file is readable
    or the container has no memory cap applied.
    """
    for path, version in [(_CGROUP_V1_PATH, "v1"), (_CGROUP_V2_PATH, "v2")]:
        try:
            with open(path, "r") as fh:
                content = fh.read().strip()
            if content == _CGROUP_MAX_SENTINEL:
                # cgroup v2 reports "max" when no limit is set
                logger.debug(
                    "cgroup %s at %s reports 'max' (no limit); skipping", version, path
                )
                continue
            limit = int(content)
            # Ignore absurdly large cgroup v1 values (kernel reports
            # PAGE_COUNTER_MAX ≈ 2^63 when no limit is configured).
            if limit <= 0 or limit >= (1 << 62):
                logger.debug(
                    "cgroup %s at %s reports implausible limit %d; skipping",
                    version,
                    path,
                    limit,
                )
                continue
            logger.info(
                "RAM limit sourced from cgroup %s (%s): %d bytes (%.0f MB)",
                version,
                path,
                limit,
                limit / (1024 * 1024),
            )
            return limit
        except (FileNotFoundError, PermissionError, OSError):
            continue
        except ValueError:
            logger.debug(
                "cgroup %s at %s contained non-integer data; skipping", version, path
            )
            continue
    return None


class MesaConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_provider: str = "claude"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    mesa_llm_provider: str = Field(
        "openai_compatible", validation_alias="MESA_LLM_PROVIDER"
    )
    llm_base_url: str | None = Field(None, validation_alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(None, validation_alias="LLM_API_KEY")
    llm_model_name: str | None = Field(
        "llama-3.1-8b-instant", validation_alias="LLM_MODEL_NAME"
    )
    embedding_dimension: int = 1536

    tiebreaker_latency_threshold_ms: float = 500.0
    bootstrap_cosine_threshold: float = 0.75
    context_window_limit: int = 8000
    lancedb_memory_limit_bytes: int = _SAFE_MODE_RAM_BYTES
    spacy_language_model: str = "xx_ent_wiki_sm"

    # Dynamic limits: fraction of total RAM allocated to LanceDB (M1)
    ram_allocation_fraction: float = 0.18

    # Cross-validation lock thresholds (Module 8)
    entity_similarity_threshold: float = 0.80
    relation_similarity_threshold: float = 0.70

    # Consolidation loop parameters (Module 8)
    consolidation_batch_size: int = 20
    hub_degree_threshold: int = 5
    consolidation_idle_timeout: int = 10
    uncertain_zone_lower_bound: float = 0.3
    anchor_interval: int = 3
    human_review_max_size: int = 1000

    # P0-A: Batch processing & token compression
    max_batch_tokens: int = 6000  # MESA_MAX_BATCH_TOKENS
    truncation_max_retries: int = 2  # MESA_TRUNCATION_MAX_RETRIES

    # Observability (Module 4)
    histogram_max_size: int = 10000
    metrics_admission_threshold: float = 0.8
    metrics_divergence_threshold: float = 0.5

    # Valence recalibration interval (Module 7)
    recalibration_interval: int = 50
    max_embedding_history: int = 500
    ecod_anomaly_threshold: float = (
        0.80  # MESA_ECOD_ANOMALY_THRESHOLD (0-1, normalized)
    )
    drift_sigmoid_weight: float = -10.0
    drift_ewmad_alpha: float = 0.2
    drift_ewmad_momentum: float = 0.8
    drift_clamp_min: float = 0.50
    drift_clamp_max: float = 0.90

    # Hybrid retrieval parameters (Module 9)
    hybrid_alpha: float = Field(0.0, validation_alias="MESA_HYBRID_ALPHA")
    hybrid_beta: float = Field(0.0, validation_alias="MESA_HYBRID_BETA")
    t_route: float = Field(0.85, validation_alias="MESA_T_ROUTE")
    cold_start_min_nodes: int = 10
    cold_start_fitness_weight: float = 0.5
    cold_start_distance_weight: float = 0.5
    ppr_alpha: float = 0.15

    # -----------------------------------------------------------------------
    # v0.4.0 Phase 3: Zero-Hallucination Legal Mode
    # When True, the AdaptiveRouter bypasses the small-model confidence gate
    # and ALWAYS routes to the Dual-LLM ConsolidationLoop.  This eliminates
    # the risk of "confident hallucinations" from lightweight models when
    # processing legal documents (statutes, case law, regulatory filings).
    # Prioritises absolute accuracy over token cost optimisation.
    # -----------------------------------------------------------------------
    legal_domain_mode: bool = Field(False, validation_alias="MESA_LEGAL_DOMAIN_MODE")

    # Local embedding fallback model (used when OpenAI key is absent)
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # -----------------------------------------------------------------------
    # v0.4.1 DX Patch: Optional REBEL Model
    # Default: DISABLED.  The 1.8 GB Babelscape/rebel-large model is NOT
    # downloaded or loaded unless explicitly opted-in via env var.
    # Triple extraction uses the LLM zero-shot prompt (Groq/Llama-3) which
    # supports domain-specific, language-aware extraction (e.g., Turkish
    # legal text) without any local model overhead.
    # Set MESA_REBEL_ENABLED=true ONLY in GPU-equipped environments where
    # the transformer pipeline is required for English-only workloads.
    # -----------------------------------------------------------------------
    rebel_enabled: bool = Field(False, validation_alias="MESA_REBEL_ENABLED")

    # -----------------------------------------------------------------------
    # v0.5.0 Phase 1.3: Domain-Specific Extraction Language
    # Controls which zero-shot extraction prompt template is used when
    # REBEL is disabled.  Supported values: "en" (English), "tr" (Turkish).
    # Turkish mode uses a specialised legal/formal prompt optimised for
    # extracting (özne, yüklem, nesne) triplets from mevzuat, kanun, and
    # yönetmelik texts.
    # -----------------------------------------------------------------------
    extraction_lang: str = Field("tr", validation_alias="MESA_EXTRACTION_LANG")

    # -----------------------------------------------------------------------
    # Resilience & Circuit Breaker (Phase 2.3)
    # -----------------------------------------------------------------------
    retry_max_attempts: int = Field(5, validation_alias="MESA_RETRY_MAX_ATTEMPTS")
    retry_min_wait_sec: int = Field(2, validation_alias="MESA_RETRY_MIN_WAIT_SEC")
    retry_max_wait_sec: int = Field(60, validation_alias="MESA_RETRY_MAX_WAIT_SEC")
    circuit_breaker_threshold: int = Field(
        10, validation_alias="MESA_CIRCUIT_BREAKER_THRESHOLD"
    )
    circuit_breaker_cooldown_sec: float = Field(
        60.0, validation_alias="MESA_CIRCUIT_BREAKER_COOLDOWN"
    )

    # -----------------------------------------------------------------------
    # Persistent Queue Paths (Phase 5.1 — P7 remediation)
    # Configurable via env vars. Defaults match the original hardcoded paths.
    # -----------------------------------------------------------------------
    human_review_queue_path: str = Field(
        "./storage/human_review_queue.jsonl",
        validation_alias="MESA_HUMAN_REVIEW_QUEUE_PATH",
    )
    dead_letter_queue_path: str = Field(
        "./storage/dead_letter_queue.jsonl",
        validation_alias="MESA_DEAD_LETTER_QUEUE_PATH",
    )

    @model_validator(mode="after")
    def validate_embedding_fallback(self) -> "MesaConfig":
        if self.llm_provider.lower() == "claude" and not self.openai_api_key:
            import logging

            logging.getLogger("MESA_Config").warning(
                "OPENAI_API_KEY not set for Claude provider. "
                "Embeddings will use local model '%s' as fallback.",
                self.local_embedding_model,
            )
        return self

    @model_validator(mode="after")
    def validate_hard_constraints(self) -> "MesaConfig":
        assert self.hybrid_alpha <= 0.5, "hybrid_alpha MUST be strictly <= 0.5"
        assert self.hybrid_beta <= 0.5, "hybrid_beta MUST be strictly <= 0.5"
        assert (
            0.5 < self.t_route < 0.99
        ), "t_route MUST be strictly between 0.5 and 0.99"
        return self


def calculate_dynamic_limits(config: MesaConfig) -> MesaConfig:
    """Resolve the system's available RAM through a hierarchical fallback chain.

    Priority order:
        1. ``psutil.virtual_memory()`` — most accurate on bare-metal / VMs.
        2. ``MESA_MAX_RAM_MB`` environment variable — operator override.
        3. Linux cgroup limits (v1 then v2) — Docker / Kubernetes awareness.
        4. Safe-mode constant (1 GB) — last resort with CRITICAL log.
    """
    total_ram: Optional[int] = None

    # --- Tier 1: psutil (host-level) ---
    try:
        total_ram = psutil.virtual_memory().total
        logger.info(
            "RAM limit sourced from psutil: %d bytes (%.0f MB)",
            total_ram,
            total_ram / (1024 * 1024),
        )
    except Exception as exc:
        logger.warning("psutil.virtual_memory() failed: %s", exc)

    # --- Tier 2: MESA_MAX_RAM_MB env var (operator override) ---
    if total_ram is None:
        total_ram = _read_env_ram_limit()

    # --- Tier 3: cgroup limits (container-aware) ---
    if total_ram is None:
        total_ram = _read_cgroup_ram_limit()

    # --- Tier 4: Safe-mode fallback (1 GB) ---
    if total_ram is None:
        total_ram = _SAFE_MODE_RAM_BYTES
        logger.critical(
            "MEMORY LIMITS UNVERIFIED: All RAM detection methods failed. "
            "Falling back to safe-mode limit of %d MB. "
            "Set MESA_MAX_RAM_MB to override.",
            _SAFE_MODE_RAM_BYTES // (1024 * 1024),
        )

    config.lancedb_memory_limit_bytes = int(total_ram * config.ram_allocation_fraction)
    return config


config = calculate_dynamic_limits(MesaConfig())  # type: ignore[call-arg]
