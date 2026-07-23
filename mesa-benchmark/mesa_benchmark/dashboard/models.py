from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BenchmarkProfile = Literal["quality", "native", "capacity"]
ShardMode = Literal["auto_duration", "fixed_count", "limits"]
JobStatus = Literal[
    "queued",
    "running",
    "paused",
    "cancelled",
    "failed",
    "completed",
]


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("Yeni benchmark", min_length=1, max_length=120)
    profile: BenchmarkProfile = "quality"
    config: str = "resource://configs/internal/smoke_dense.yaml"
    clients: list[str] = Field(default_factory=lambda: ["mesa", "dense-rag", "mem0"])
    seed: int = 42
    generator_model: str | None = None
    iterations: int = Field(1, ge=1, le=20)
    top_k: int = Field(5, ge=1, le=100)
    context_token_budget: int = Field(4096, ge=128, le=262_144)
    generation_enabled: bool | None = None
    generation_temperature: float = Field(0.0, ge=0.0, le=2.0)
    judge_enabled: bool = False
    judge_model: str | None = None
    shard_mode: ShardMode = "auto_duration"
    target_shard_minutes: int = Field(20, ge=1, le=1_440)
    shard_count: int | None = Field(None, ge=1, le=10_000)
    shard_question_limit: int = Field(100, ge=1, le=10_000)
    shard_context_limit: int = Field(1_000, ge=1, le=100_000)
    time_limit_minutes: float | None = Field(None, gt=0.0, le=10_080)
    warmup_enabled: bool = True

    @field_validator("clients")
    @classmethod
    def validate_clients(cls, value: list[str]) -> list[str]:
        supported = {"mesa", "dense-rag", "mem0", "letta", "zep"}
        normalized = list(dict.fromkeys(value))
        unknown = set(normalized).difference(supported)
        if unknown:
            raise ValueError(f"unsupported clients: {sorted(unknown)}")
        if not normalized:
            raise ValueError("at least one client is required")
        return normalized

    @field_validator("judge_model")
    @classmethod
    def validate_judge_model(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["pause", "cancel"]


class TimeExtensionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: float | None = Field(None, gt=0.0, le=10_080)
    remove_limit: bool = False


class OllamaSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    model: str | None = Field(None, max_length=200)


class OllamaTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)


class DatasetSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool


class DashboardJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    profile: BenchmarkProfile
    status: JobStatus
    created_at: str
    updated_at: str
    progress: float = Field(0.0, ge=0.0, le=100.0)
    eta_seconds: int | None = None
    eta_confidence: Literal["düşük", "orta", "yüksek"] = "düşük"
    current_task: str | None = None
    archived: bool = False
    error: str | None = None
    plan_path: str
    event_path: str
    pid: int | None = None
    result: dict[str, Any] | None = None
    queue_position: int | None = None
    started_at: str | None = None
    active_elapsed_seconds: float = 0.0
    time_limit_minutes: float | None = None
    pause_reason: str | None = None
    progress_snapshot: dict[str, Any] | None = None
    provisional_result: dict[str, Any] | None = None


class DatasetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    dataset_id: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: str
    updated_at: str
    progress: float = Field(0.0, ge=0.0, le=100.0)
    pid: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
