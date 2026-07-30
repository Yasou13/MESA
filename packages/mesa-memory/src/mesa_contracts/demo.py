"""Contracts for the explicitly enabled local Showcase demo."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DemoChatRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    agent_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=4096)


class DemoChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    response_text: str
    context: list[dict]
    latency_ms: int
    memory_stored: bool
