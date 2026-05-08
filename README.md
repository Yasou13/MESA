# MESA: Cognitive Memory Engine for High-Security Clinical Environments

## 1. Mission & Value Proposition

**MESA (Medical Entity Storage Architecture)** is a highly resilient, asynchronous, and scalable cognitive memory system. While the core architecture serves as an advanced, high-dimensional memory engine, it is explicitly engineered and deployed as a "Zero-Hallucination" clinical memory engine for High-Security Medical Environments (HIS/EMR).

General-purpose RAG (Retrieval-Augmented Generation) systems fail in clinical settings because they lack strict referential integrity and deterministic safeguards. MESA bridges this gap. By leveraging a robust, asynchronous graph-vector consolidation core, MESA enables absolute cross-verification of medical entities. It is engineered for clinical triage, EMR integration, and physician support—ensuring that Protected Health Information (PHI) is processed with "Local-First" prioritization and that generated insights are cryptographically and contextually verifiable, permanently eliminating generative hallucinations.

## 2. Core Architectural Modules

The system is fortified by three primary engines, engineered to prevent the contamination of the cognitive graph and ensure clinical relevance.

### Valence Motor (The Gatekeeper)
To prevent "junk data" ingestion, the Valence Motor acts as the deterministic first line of defense. It utilizes mathematically rigorous models for anomaly detection before data enters the memory pipeline:
*   **ECOD (Empirical Cumulative distribution functions for Outlier Detection):** Rapidly identifies mathematically anomalous or syntactically corrupt patterns in incoming cognitive streams.
*   **EWMAD (Exponentially Weighted Moving Average Deviation):** A dynamic thresholding algorithm that recalibrates continuously, ensuring that only highly salient, medically relevant information crosses the threshold into the storage layer.

### Asymmetric Hybrid LLM (Tier-0 Routing)
MESA utilizes a highly optimized, cost-effective, and secure routing layer to balance intelligence with strict data privacy.
*   **Local SLMs (e.g., Qwen, Gemma):** All PHI and initial clinical triage processing are routed exclusively to localized, self-hosted Small Language Models (Tier-0). This guarantees air-gapped HIPAA/GDPR compliance.
*   **Cloud Models (e.g., Claude 3.5 Sonnet):** Only scrubbed, anonymized, and heavily abstracted tasks requiring complex reasoning are elevated to Tier-1 Cloud LLMs for deterministic graph extraction and cross-verification.

### Cognitive Memory Block (CMB)
The fundamental atomic unit of MESA is the Cognitive Memory Block (CMB). Going beyond simple text vectors, CMBs inherently embed affective state metrics to map the clinical focus and patient urgency during encounters:
*   `cat7_focus`: Tracks the concentration and context depth of the interaction.
*   `mood_valence`: Analyzes sentiment to prioritize acute, distressing, or unstable patient states.
*   `arousal`: Measures the intensity or urgency of the clinical scenario, feeding directly into automated triage priorities.

## 3. Setup & Configuration

MESA requires strict initialization via environment variables. The `MESA_` prefix is mandatory for all settings to prevent namespace collisions in complex EMR deployment environments.

Create a `.env` file in the project root:

```env
# Required API Keys
MESA_OPENAI_API_KEY=your_secure_openai_key
MESA_ANTHROPIC_API_KEY=your_secure_anthropic_key

# Asymmetric Routing Configuration
MESA_LOCAL_LLM_ENDPOINT=http://localhost:11434/api/generate
MESA_TIER0_MODEL=qwen2.5:7b-instruct-q4_0

# Consolidation Engine Constraints
# CRITICAL: Do not exceed 20. Forced cap by Pydantic validation & RAM constraints.
MESA_CONSOLIDATION_BATCH_SIZE=20
MESA_CONSOLIDATION_TIMEOUT_MS=30000

# Storage Locations
MESA_DB_PATH=./data/raw_log.db
MESA_VECTOR_PATH=./data/vector_index.lance
```

## 4. Reliability & Security

MESA is designed upon a "Zero Trust" architecture, assuming both user inputs and LLM generative outputs are potentially hostile, non-deterministic, or malformed.

*   **`reconcile_orphans` Protocol:** To prevent data desynchronization between the primary SQLite Raw Log and the LanceDB Vector Index, MESA employs a background reconciliation protocol. Any orphaned vectors (where the SQLite record was corrupted) or unindexed records are detected and resolved via scheduled background syncs, ensuring 100% referential integrity.
*   **Zero Trust Data Handling & Prompt Injection Filtering:** All incoming clinical notes undergo strict sanitization before hitting the embedding model or Tier-0 routers. System prompts are structurally isolated from user data payloads to prevent Prompt Injection attacks that could induce data exfiltration or corrupt the cognitive graph.

## 5. Testing Strategy

Quality assurance in MESA targets failure modes unique to large-scale, batch-oriented memory systems.

*   **"Lost-in-the-Middle" Verification:** Specialized test suites inject critical clinical details into the middle of massive contextual noise. These tests ensure the Asymmetric LLM routers and the Consolidation engine accurately extract hidden entities, preventing the notorious "Lost-in-the-Middle" amnesia common in standard batch processing.
*   **Data Retention Integrity Testing:** Rigorous read/write audits, simulated process terminations during batch consolidation, and automated rollback testing guarantee that once a CMB passes the Valence Motor, it is permanently and accurately retrievable.

---
*MESA Architecture is proprietary. Designed for highly resilient, zero-hallucination clinical environments.*
