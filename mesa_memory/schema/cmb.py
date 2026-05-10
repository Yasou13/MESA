from typing import List, Optional
from datetime import datetime, timezone

from uuid6 import uuid7 as _uuid7_func
from pydantic import BaseModel, ConfigDict, Field


class ResourceCost(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_count: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)


class AffectiveState(BaseModel):
    model_config = ConfigDict(frozen=True)

    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.0, ge=0.0, le=1.0)


def generate_uuid7() -> str:
    return str(_uuid7_func())


class CMB(BaseModel):
    model_config = ConfigDict(frozen=True)

    cmb_id: str = Field(default_factory=generate_uuid7)
    schema_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_payload: str = Field(min_length=1)
    source: str = Field(min_length=1)
    performative: str = Field(min_length=1)
    cat7_focus: float = Field(default=0.5, ge=0.0, le=1.0)
    cat7_mood: AffectiveState = Field(default_factory=AffectiveState)
    prediction_error_score: float = Field(default=0.0, ge=0.0, le=1.0)
    resource_cost: ResourceCost
    fitness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding: List[float] = Field(default_factory=list)
    parent_cmb_id: Optional[str] = None
    tier3_deferred: bool = Field(default=False)
