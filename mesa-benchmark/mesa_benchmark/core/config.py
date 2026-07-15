from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field

from .exceptions import ConfigurationError


class DatasetConfig(BaseModel):
    name: str = Field(..., description="Name of the dataset to be loaded.")
    version: str = Field("v1", description="Version of the dataset.")
    path: str = Field(
        ..., description="Relative or absolute path to the dataset folder/file."
    )
    noise_ratio: float = Field(
        0.0, ge=0.0, le=1.0, description="Ratio of noise to be injected (0.0 to 1.0)."
    )


class ClientConfig(BaseModel):
    name: str = Field(
        ..., description="Name of the target memory system (e.g., 'mesa', 'mem0')."
    )
    adapter_class: str = Field(
        ..., description="Module path to the client adapter class."
    )
    timeout_ms: int = Field(
        10000, description="Timeout for client operations in milliseconds."
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Client-specific parameters."
    )


class EvaluationConfig(BaseModel):
    metrics: list[str] = Field(
        default=["hit_at_k", "mrr", "latency", "efficiency"],
        description="List of metrics to compute.",
    )
    llm_judge_model: Optional[str] = Field(
        None, description="LLM model to use for single-model judging (if applicable)."
    )
    multi_judge_models: list[str] = Field(
        default_factory=list,
        description="List of LLM models for multi-model independent judging.",
    )
    enable_agreement: bool = Field(
        False,
        description="If true, compute agreement rate between keyword/exact-match and LLM-judge evaluators.",
    )


class BenchmarkConfig(BaseModel):
    suite_name: str = Field(..., description="Name of the benchmark suite run.")
    iterations: int = Field(
        5,
        description="Number of times to run the benchmark for statistical significance.",
    )
    seed: int = Field(42, description="Random seed for reproducibility.")
    dataset: DatasetConfig
    client: ClientConfig
    evaluation: EvaluationConfig


import os
import re

def _resolve_env_vars(obj: Any) -> Any:
    if isinstance(obj, str):
        pattern = re.compile(r'\$\{([^}]+)\}')
        def replace(match):
            var_name = match.group(1)
            if var_name not in os.environ:
                raise ConfigurationError(f"Environment variable '{var_name}' is not set but referenced in config.")
            return os.environ[var_name]
        return pattern.sub(replace, obj)
    elif isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def load_config(file_path: str | Path) -> BenchmarkConfig:
    """Loads and validates the benchmark configuration from a YAML file."""
    path = Path(file_path)
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Failed to parse YAML file: {e}")

    if not isinstance(data, dict):
        raise ConfigurationError("YAML file must contain a root dictionary.")

    data = _resolve_env_vars(data)

    try:
        return BenchmarkConfig(**data)
    except Exception as e:
        raise ConfigurationError(f"Configuration validation failed: {e}")
