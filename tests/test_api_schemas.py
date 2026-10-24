"""Tests for strict Pydantic V2 schemas in mesa_api/schemas.py."""

import pytest
from pydantic import ValidationError

from mesa_api.schemas import ErrorResponse


class TestErrorResponse:
    def test_error_response_valid(self):
        resp = ErrorResponse(
            error="VALIDATION_ERROR",
            detail="Invalid payload",
            status_code=422,
            retryable=False,
        )
        assert resp.error == "VALIDATION_ERROR"
        assert resp.detail == "Invalid payload"
        assert resp.status_code == 422
        assert resp.retryable is False

    def test_error_response_status_code_bounds(self):
        with pytest.raises(ValidationError):
            ErrorResponse(
                error="ERR",
                detail="low status",
                status_code=399,
            )
        with pytest.raises(ValidationError):
            ErrorResponse(
                error="ERR",
                detail="high status",
                status_code=600,
            )

    def test_error_response_frozen(self):
        resp = ErrorResponse(
            error="ERR",
            detail="msg",
            status_code=400,
        )
        with pytest.raises(ValidationError):
            resp.error = "NEW_ERR"  # type: ignore[misc]
