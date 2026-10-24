from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """Standardised error response for all API error paths."""

    model_config = ConfigDict(frozen=True)

    error: str = Field(..., description="Machine-readable error code")
    detail: str = Field(..., description="Human-readable error description")
    status_code: int = Field(..., ge=400, le=599)
    retryable: bool = Field(
        default=False, description="Whether retrying unchanged may succeed later"
    )
