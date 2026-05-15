import re
import logging
import sqlite3
import os

from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID

logger = logging.getLogger("MESA_Security")

# ---------------------------------------------------------------------------
# Advisory prompt injection patterns — logged for observability but NOT used
# to hard-block content.  MESA's primary injection defense is architectural:
# all user content is interpolated inside <CONTENT>…</CONTENT> sandbox tags
# in the LLM prompt templates (see consolidation/loop.py), so the model is
# explicitly instructed to treat the block as untrusted data.
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?previous\s+(instructions|context|rules|prompts)",
    r"(?i)disregard\s+(all\s+)?(prior|previous|above)",
    r"(?i)forget\s+(all\s+)?(previous|prior|above|your)\s+(instructions|rules|context)",
    r"(?i)override\s+(safety|security|restrictions|guidelines|filters)",
    r"(?i)\bDAN\b.*\bmode\b",
    r"(?i)do\s+anything\s+now",
    r"(?i)jailbreak",
    r"(?i)reveal\s+(your|the|all)\s+(system|hidden|secret)\s+(prompt|instructions|rules)",
    r"(?i)\[INST\]|\[/INST\]|<<SYS>>|<\|im_start\|>",
]


class PromptInjectionError(ValueError):
    """Raised when prompt injection is detected in content."""

    pass


def detect_prompt_injection(content: str) -> bool:
    """Check content for known prompt injection patterns (advisory only).

    Returns True if any pattern matches.  Callers may log or flag the
    content but should NOT hard-block it — false positives are common
    in conversational agent systems.
    """
    return any(re.search(p, content) for p in INJECTION_PATTERNS)


class AccessControl:
    def __init__(self, policy_path: str = "./storage/rbac_policy.db"):
        self.policy_path = policy_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.policy_path)), exist_ok=True)
        with sqlite3.connect(self.policy_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS permissions (
                    agent_id TEXT,
                    session_id TEXT,
                    access_level TEXT,
                    PRIMARY KEY (agent_id, session_id)
                )
            """
            )
            # Seed the reserved system daemon identity with WRITE access
            conn.execute(
                "INSERT OR IGNORE INTO permissions (agent_id, session_id, access_level) VALUES (?, ?, ?)",
                (SYSTEM_AGENT_ID, SYSTEM_SESSION_ID, "WRITE"),
            )

    def grant_access(self, agent_id: str, session_id: str, level: str):
        if level not in ("READ", "WRITE"):
            raise ValueError(
                f"Invalid access level: {level}. Must be 'READ' or 'WRITE'."
            )
        with sqlite3.connect(self.policy_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO permissions (agent_id, session_id, access_level) VALUES (?, ?, ?)",
                (agent_id, session_id, level),
            )

    def revoke_access(self, agent_id: str, session_id: str):
        with sqlite3.connect(self.policy_path) as conn:
            conn.execute(
                "DELETE FROM permissions WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            )

    def check_access(self, agent_id: str, session_id: str, required_level: str) -> bool:
        with sqlite3.connect(self.policy_path) as conn:
            cursor = conn.execute(
                "SELECT access_level FROM permissions WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            )
            row = cursor.fetchone()
            if not row:
                return False
            granted = row[0]
            if required_level == "READ":
                return granted in ("READ", "WRITE")
            if required_level == "WRITE":
                return granted == "WRITE"
            return False


def sanitize_cmb_content(content: str) -> str:
    """Standard input normalization for CMB content payloads.

    Performs the following transformations:
    1. Strips null bytes (binary safety).
    2. Strips ANSI escape sequences (terminal injection).
    3. Strips dangerous HTML tags AND their content (script, style, iframe, etc.).
    4. Strips remaining HTML tags (preserves text content).
    5. Neutralizes common shell metacharacters (backticks, $()).
    6. Normalizes whitespace to single spaces.
    7. Advisory-only prompt injection logging (no hard block).
    """
    # 1. Null bytes
    content = content.replace("\x00", "")

    # 2. ANSI escape sequences
    content = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", content)

    # 3. Strip dangerous tags AND their content (script, style, etc.)
    content = re.sub(
        r"<(script|style|iframe|object|embed)[^>]*>.*?</\1>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 4. Strip remaining HTML tags
    content = re.sub(r"<[^>]*>", "", content)

    # 5. Neutralize shell metacharacters
    content = content.replace("`", "'")
    content = re.sub(r"\$\(", "(", content)

    # 6. Normalize whitespace
    content = " ".join(content.split())

    # 7. Advisory logging — flag but do not block
    if detect_prompt_injection(content):
        logger.info(
            "PROMPT_INJECTION_ADVISORY: Content matched injection heuristic "
            "(advisory only — content passed through)"
        )

    return content
