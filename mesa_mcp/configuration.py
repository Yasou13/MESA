"""Validated, fail-closed configuration for the MESA MCP process."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]{1,96}$")


class MCPSettings(BaseSettings):
    """Settings controlled by the local MCP host, never by a tool caller."""

    model_config = SettingsConfigDict(extra="ignore")

    base_url: str = Field(default="http://localhost:8000", alias="MESA_BASE_URL")
    api_key: str | None = Field(default=None, alias="MESA_API_KEY")
    namespace: str = Field(default="local", alias="MESA_NAMESPACE")
    actor_id: str = Field(default="antigravity-agent", alias="MESA_ACTOR_ID")
    default_project_id: str = Field(default="mesa", alias="MESA_PROJECT_ID")
    workspace_root: Path = Field(alias="MESA_WORKSPACE_ROOT")
    search_default_limit: int = Field(default=8, ge=1, le=20, alias="MESA_SEARCH_DEFAULT_LIMIT")
    search_max_limit: int = Field(default=20, ge=1, le=20, alias="MESA_SEARCH_MAX_LIMIT")
    context_default_token_budget: int = Field(
        default=2500, ge=1, le=8000, alias="MESA_CONTEXT_DEFAULT_TOKEN_BUDGET"
    )
    context_max_token_budget: int = Field(
        default=8000, ge=1, le=8000, alias="MESA_CONTEXT_MAX_TOKEN_BUDGET"
    )
    log_level: str = Field(default="INFO", alias="MESA_LOG_LEVEL")

    @field_validator("namespace", "actor_id", "default_project_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be 1-96 characters of letters, digits, '.', '_' or '-'")
        return value

    @field_validator("workspace_root")
    @classmethod
    def validate_workspace_root(cls, value: Path) -> Path:
        root = value.expanduser().resolve(strict=False)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("must be an existing absolute directory")
        return root

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must use http:// or https://")
        return value

    def session_id_for(self, project_id: str) -> str:
        """Return the internal MESA session scope for a project.

        The MCP caller never supplies an actor or namespace to MESA directly.
        """
        normalized = project_id.strip()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("project_id must be 1-96 safe identifier characters")
        session_id = f"mcp-{self.namespace}-{normalized}"
        if len(session_id) > 128:
            raise ValueError("project_id produces an invalid MESA session identifier")
        return session_id
