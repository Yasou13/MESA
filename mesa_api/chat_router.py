"""OpenAI-compatible /v1/chat/completions endpoint backed by MESA D103 ask (D104)."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mesa_api.identifier_validation import SourceIdentifier
from mesa_api.v4_router import V4AskRequest, execute_v4_ask
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO

_SCOPE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_NOT_FOUND_TEXT = (
    "I could not find an answer to that question in the available sources."
)


class OpenAIChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = Field(min_length=1, max_length=64)
    content: Any = ""


class OpenAIChatCompletionsRequest(BaseModel):
    """OpenAI-compatible chat completions request body (D104)."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(default="mesa", min_length=1, max_length=512)
    messages: list[OpenAIChatMessage] = Field(min_length=1)
    stream: bool = False
    document_ids: list[SourceIdentifier] | None = Field(
        default=None, min_length=1, max_length=64
    )
    valid_at: datetime | None = None
    limit: int = Field(default=8, ge=1, le=50)
    expand: Literal["none", "neighbors", "article"] = "none"
    retrieval_mode: Literal["single", "adaptive"] = "single"


def _resolve_model_scope(model: str) -> tuple[str, str, str]:
    """Parse ``mesa`` or ``mesa:<tenant>/<workspace>/<dataset>`` into scope tuple."""
    cleaned = model.strip()
    if cleaned in {"mesa", "mesa:default"}:
        return (
            os.environ.get("MESA_TENANT_ID") or "local",
            os.environ.get("MESA_WORKSPACE_ID") or "default",
            os.environ.get("MESA_DATASET_ID") or "default",
        )
    if not cleaned.startswith("mesa:"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown model '{model}'. "
                "Use 'mesa' or 'mesa:<tenant>/<workspace>/<dataset>'."
            ),
        )
    parts = cleaned[len("mesa:") :].split("/")
    if len(parts) != 3 or any(not _SCOPE_SEGMENT_RE.fullmatch(part) for part in parts):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Invalid model scope '{model}'. "
                "Expected 'mesa:<tenant>/<workspace>/<dataset>'."
            ),
        )
    return parts[0], parts[1], parts[2]


def _extract_last_user_message(messages: list[OpenAIChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role.strip().lower() != "user":
            continue
        raw = msg.content
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                return text
        elif isinstance(raw, list):
            parts: list[str] = []
            for part in raw:
                if isinstance(part, dict) and part.get("type") == "text":
                    piece = str(part.get("text") or "").strip()
                    if piece:
                        parts.append(piece)
                elif isinstance(part, str) and part.strip():
                    parts.append(part.strip())
            joined = "\n".join(parts).strip()
            if joined:
                return joined
    raise HTTPException(
        status_code=422, detail="At least one non-empty user message is required"
    )


def _format_cited_answer(ask_result: dict[str, Any]) -> str:
    if ask_result.get("not_found"):
        return _NOT_FOUND_TEXT
    answer = str(ask_result.get("answer") or "").strip()
    citations = ask_result.get("citations") or []
    if not citations:
        return answer or _NOT_FOUND_TEXT
    lines = [answer, "", "Sources:"]
    for idx, cit in enumerate(citations, start=1):
        doc_id = cit.get("document_id") or "unknown"
        details: list[str] = []
        if cit.get("revision_number") is not None:
            details.append(f"rev {cit['revision_number']}")
        span = cit.get("span")
        if isinstance(span, dict) and "start" in span and "end" in span:
            details.append(f"span {span['start']}..{span['end']}")
        if cit.get("page") is not None:
            details.append(f"p.{cit['page']}")
        if cit.get("sheet") is not None:
            row_part = (
                f" rows {cit['row_start']}..{cit['row_end']}"
                if cit.get("row_start") is not None
                else ""
            )
            details.append(f"sheet {cit['sheet']}{row_part}")
        valid_from = cit.get("valid_from") or ""
        valid_to = cit.get("valid_to") or ""
        if valid_from or valid_to:
            details.append(f"valid {valid_from or '..'}..{valid_to or 'present'}")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"[{idx}] {doc_id}{suffix}")
    return "\n".join(lines)


def create_chat_router(
    *,
    get_dao: Callable[[], MemoryDAO],
    get_access_control: Callable[[], AccessControl],
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["openai-compatible"])

    @router.post("/chat/completions", response_model=None)
    async def chat_completions(
        request: Request,
        payload: OpenAIChatCompletionsRequest,
        dao: MemoryDAO = Depends(get_dao),
        access_control: AccessControl = Depends(get_access_control),
    ) -> Any:
        tenant_id, workspace_id, dataset_id = _resolve_model_scope(payload.model)
        question = _extract_last_user_message(payload.messages)

        ask_request = V4AskRequest(
            question=question,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_ids=[dataset_id],
            document_ids=payload.document_ids,
            valid_at=payload.valid_at,
            limit=payload.limit,
            expand=payload.expand,
            retrieval_mode=payload.retrieval_mode,
        )
        ask_result = await execute_v4_ask(
            request=request,
            payload=ask_request,
            dao=dao,
            access_control=access_control,
        )

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        content = _format_cited_answer(ask_result)
        citations = list(ask_result.get("citations") or [])
        usage_raw = ask_result.get("usage") or {}
        usage = {
            "prompt_tokens": int(usage_raw.get("prompt_tokens", 0)),
            "completion_tokens": int(usage_raw.get("completion_tokens", 0)),
            "total_tokens": int(usage_raw.get("total_tokens", 0)),
        }

        if payload.stream:
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
                "mesa_citations": citations,
                "mesa_not_found": bool(ask_result.get("not_found", False)),
            }

            async def _sse_body() -> Any:
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_sse_body(), media_type="text/event-stream")

        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": payload.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
            "mesa_citations": citations,
            "mesa_not_found": bool(ask_result.get("not_found", False)),
        }

    return router
