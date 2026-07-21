import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .exceptions import ConfigurationError


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(..., description="Name of the dataset to be loaded.")
    version: str = Field("v1", description="Version of the dataset.")
    path: str = Field(
        ..., description="Relative or absolute path to the dataset folder/file."
    )
    noise_ratio: float = Field(
        0.0, ge=0.0, le=1.0, description="Ratio of noise to be injected (0.0 to 1.0)."
    )


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(
        ..., description="Name of the target memory system (e.g., 'mesa', 'mem0')."
    )
    adapter_class: str = Field(
        ..., description="Module path to the client adapter class."
    )
    timeout_ms: int = Field(
        10000, gt=0, description="Timeout for client operations in milliseconds."
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Client-specific parameters."
    )


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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
    judge_timeout_s: float = Field(120.0, gt=0.0)
    judge_ensemble_size: int = Field(3, ge=1, le=9)
    judge_quorum: Optional[int] = Field(None, ge=1)
    judge_max_concurrency: int = Field(3, ge=1, le=3)


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: Optional[str] = None
    timeout_s: float = Field(120.0, gt=0.0)
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: int = Field(5, ge=1, le=100)
    ollama_url: Optional[str] = None
    require_independent_judge: bool = True


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    suite_name: str = Field(..., description="Name of the benchmark suite run.")
    iterations: int = Field(
        5,
        ge=1,
        description="Number of times to run the benchmark for statistical significance.",
    )
    seed: int = Field(42, description="Random seed for reproducibility.")
    dataset: DatasetConfig
    client: ClientConfig
    evaluation: EvaluationConfig
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @model_validator(mode="after")
    def validate_judge_quorum(self) -> "BenchmarkConfig":
        quorum = self.evaluation.judge_quorum
        size = self.evaluation.judge_ensemble_size
        if quorum is not None and quorum > size:
            raise ValueError("judge_quorum cannot exceed judge_ensemble_size")
        return self


def apply_runtime_environment(config: BenchmarkConfig) -> None:
    """Derive provider-specific variables from one canonical Ollama URL."""
    base_url = config.runtime.ollama_url or os.environ.get("BENCHMARK_OLLAMA_URL")
    if not base_url:
        return
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    os.environ["BENCHMARK_OLLAMA_URL"] = base_url
    os.environ["MESA_OLLAMA_URL"] = base_url
    os.environ["OLLAMA_HOST"] = base_url
    os.environ["OPENAI_BASE_URL"] = f"{base_url}/v1"


def _resolve_env_vars(obj: Any, environ: Mapping[str, str]) -> Any:
    if isinstance(obj, str):
        pattern = re.compile(r"\$\{([^}]+)\}")

        def replace(match: Any) -> str:
            var_name = match.group(1)
            if var_name not in environ:
                raise ConfigurationError(
                    f"Environment variable '{var_name}' is not set but referenced in config."
                )
            return environ[var_name]

        return pattern.sub(replace, obj)
    elif isinstance(obj, dict):
        return {k: _resolve_env_vars(v, environ) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_env_vars(v, environ) for v in obj]
    return obj


def _apply_environment_overrides(
    data: Dict[str, Any], environ: Mapping[str, str]
) -> Dict[str, Any]:
    """Overlay supported runtime values before Pydantic validation.

    Keeping this transformation on the raw mapping makes the returned config
    immutable and prevents a validated model from being silently mutated.
    """
    resolved = dict(data)
    evaluation = dict(resolved.get("evaluation") or {})
    generation = dict(resolved.get("generation") or {})
    runtime = dict(resolved.get("runtime") or {})
    if judge_model := environ.get("BENCHMARK_JUDGE_MODEL"):
        evaluation["llm_judge_model"] = judge_model
    if judge_models := environ.get("BENCHMARK_JUDGE_MODELS"):
        evaluation["multi_judge_models"] = [
            item.strip() for item in judge_models.split(",") if item.strip()
        ]
    if generator_model := environ.get("BENCHMARK_GENERATOR_MODEL"):
        generation["model"] = generator_model
    if ollama_url := environ.get("BENCHMARK_OLLAMA_URL"):
        runtime["ollama_url"] = ollama_url
    resolved["evaluation"] = evaluation
    resolved["generation"] = generation
    resolved["runtime"] = runtime
    return resolved


def load_config(
    file_path: str | Path, *, environ: Optional[Mapping[str, str]] = None
) -> BenchmarkConfig:
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

    environment = os.environ if environ is None else environ
    data = _resolve_env_vars(data, environment)
    data = _apply_environment_overrides(data, environment)

    try:
        return BenchmarkConfig(**data)
    except Exception as e:
        raise ConfigurationError(f"Configuration validation failed: {e}")
