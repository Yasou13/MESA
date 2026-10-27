from __future__ import annotations

import mesa_client
from mesa_client import RECOMMENDED_SYSTEM_PROMPT
from mesa_mcp.server import create_application
from mesa_memory.security.untrusted_memory import TAG_CLOSE, TAG_OPEN


def test_recommended_system_prompt_exported_in_all():
    assert "RECOMMENDED_SYSTEM_PROMPT" in mesa_client.__all__


def test_recommended_system_prompt_tags_match_security_module():
    assert TAG_OPEN in RECOMMENDED_SYSTEM_PROMPT
    assert TAG_CLOSE in RECOMMENDED_SYSTEM_PROMPT
    assert TAG_OPEN == "<UNTRUSTED_MEMORY_EVIDENCE>"
    assert TAG_CLOSE == "</UNTRUSTED_MEMORY_EVIDENCE>"


def test_recommended_system_prompt_content_and_rules():
    prompt = RECOMMENDED_SYSTEM_PROMPT
    lines = [line for line in prompt.strip().splitlines() if line.strip()]
    assert 8 <= len(lines) <= 14, f"Expected about 10 lines, got {len(lines)}"

    lower_prompt = prompt.lower()
    assert "data, never instructions" in lower_prompt or "data" in lower_prompt
    assert "evidence" in lower_prompt
    assert "cite" in lower_prompt
    assert "document" in lower_prompt
    assert "revision" in lower_prompt
    assert "article" in lower_prompt
    assert "never invent" in lower_prompt or "not answer" in lower_prompt


def test_mcp_server_instructions_match_recommended_prompt():
    server, _ = create_application()
    assert server.instructions == RECOMMENDED_SYSTEM_PROMPT
