# MESA

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/python-app.yml/badge.svg)](https://github.com/Yasou13/MESA/actions)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

**Enterprise-Grade Cognitive Memory Engine for Autonomous AI Agents**

MESA is a next-generation cognitive memory engine designed for enterprise AI agent systems. By leveraging a multi-tiered validation architecture with asymmetric dual-LLM consensus, MESA statistically minimizes hallucination cascades and enforces structured data fidelity across long-running autonomous workflows.

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/Yasou13/MESA.git
cd mesa

# 2. Create a virtual environment and activate it
python3 -m venv venv
source venv/bin/activate

# 3. Install strictly required dependencies
pip install -r requirements.txt

# 4. Run the test suite to verify the installation
pytest tests/ -v
```

## Architecture Overview

MESA's memory pipeline processes every incoming Cognitive Memory Block (CMB) through a layered validation stack before committing to persistent storage:

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| **Tier 1** | Valence Gate | Fast statistical fitness scoring; rejects trivially low-value inputs |
| **Tier 2** | ECOD Anomaly Detection | Unsupervised outlier detection on embedding distributions |
| **Tier 3** | Dual-LLM Consensus | Two independent LLMs evaluate STORE/DISCARD; disagreements are discarded (fail-safe) |
| **Consolidation** | Knowledge Graph Extraction | Triplet extraction via REBEL (local, zero-cost) with LLM fallback; cross-validated and committed to the graph |

### Storage Layer

- **Raw Log**: SQLite with WAL mode for crash-safe append-only journaling
- **Vector Index**: LanceDB for embedding-based similarity retrieval with dynamic RAM budgeting
- **Knowledge Graph**: NetworkX for in-memory graph processing with SQLite-backed persistence and MVCC node versioning

## Feature Comparison

| Feature | MESA | LangChain Memory | MemGPT |
|---------|------|-------------------|--------|
| **Primary Focus** | Enterprise Agent Memory | General Purpose | Long-term Personas |
| **Hallucination Mitigation** | Dual-LLM Consensus + Fail-safe Discard | Prompt-based | Self-Correction |
| **Validation Architecture** | 3-Tier Statistical + LLM Pipeline | None | Prompt-based |
| **Consistency Checking** | Asymmetric Dual-LLM Cross-Validation | None | Single-model |
| **Graph Knowledge** | Automated Triplet Extraction (REBEL + LLM) | Manual | None |
| **Local-First** | Yes (NetworkX, SQLite, LanceDB) | Cloud-dependent | Cloud-dependent |

### Core Innovations

- **Asymmetric Dual-LLM Validation**: Two distinct language models independently extract knowledge triplets from memory candidates. A composite similarity score determines consensus — only triplets that exceed the validation threshold are committed to the knowledge graph. This cross-examination architecture statistically minimizes single-model hallucination cascades.
- **REBEL Zero-Cost Extraction**: A local Babelscape/rebel-large seq2seq model handles deterministic triplet extraction at zero token cost, with expensive LLM calls reserved as a fallback for records REBEL cannot process.
- **Hybrid Retrieval (RRF + PPR)**: Combines vector similarity search with Personalized PageRank over the knowledge graph using Reciprocal Rank Fusion, enabling semantically and structurally aware memory recall.

## Contributing

We welcome community contributions! Please review our [Contribution Guidelines](CONTRIBUTING.md) for details on our mandatory **Fork -> Feature Branch -> Pytest -> Pull Request** workflow.

## License

This project is licensed under the [MIT License](LICENSE) - Copyright (c) 2026 MESA Core Team.
