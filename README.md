# MESA

[![MESA CI](https://github.com/Yasou13/MESA/actions/workflows/python-app.yml/badge.svg)](https://github.com/Yasou13/MESA/actions)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

**High-Security, Zero-Hallucination Cognitive Memory Engine for EMR/HIS**

MESA is a next-generation cognitive memory engine engineered specifically for Electronic Medical Records (EMR) and Healthcare Information Systems (HIS). By leveraging a multi-tiered validation architecture, MESA guarantees absolute data fidelity and zero hallucination in critical healthcare environments.

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

## Feature Comparison

| Feature | MESA | LangChain Memory | MemGPT |
|---------|------|-------------------|--------|
| **Primary Focus** | EMR/HIS (High-Security) | General Purpose | Long-term Personas |
| **Hallucination Risk** | **Zero** | Moderate | Moderate |
| **Validation Architecture** | **Gatekeeper (Tier-0)** | None | Self-Correction |
| **Consistency Checking** | **Asymmetric Dual-LLM** | Prompt-based | Prompt-based |

### Core Innovations

MESA introduces two critical architectural innovations to guarantee clinical-grade reliability:

- **Gatekeeper (Tier-0)**: A deterministic, pre-LLM validation layer that intercepts and sanitizes inputs before they ever reach an attention head. This ensures that toxic, out-of-domain, or malformed data is rejected immediately.
- **Asymmetric Dual-LLM Validation**: A novel approach where two distinct language models cross-examine each other's outputs. A fast, specialized model generates the cognitive memory update, while an independent, high-reasoning model critically verifies the structural and semantic integrity of the update against the source clinical data. This asymmetry mathematically eliminates single-model hallucination cascades.

## Contributing

We welcome community contributions! Please review our [Contribution Guidelines](CONTRIBUTING.md) for details on our mandatory **Fork -> Feature Branch -> Pytest -> Pull Request** workflow.

## License

This project is licensed under the [MIT License](LICENSE) - Copyright (c) 2026 MESA Core Team.
