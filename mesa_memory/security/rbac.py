import re
import logging
import json
import os

logger = logging.getLogger("MESA_Security")

# Prompt injection detection patterns — adversarial instructions
# that attempt to hijack LLM behavior when interpolated into prompts.
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?previous\s+(instructions|context|rules|prompts)",
    r"(?i)disregard\s+(all\s+)?(prior|previous|above)",
    r"(?i)forget\s+(all\s+)?(previous|prior|above|your)\s+(instructions|rules|context)",
    r"(?i)you\s+are\s+now\s+a",
    r"(?i)act\s+as\s+if\s+you\s+have\s+no\s+(restrictions|rules|guidelines)",
    r"(?i)system\s*:\s*",
    r"(?i)return\s+all\s+(passwords|secrets|keys|credentials|tokens|api.?keys)",
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
    """Check content for known prompt injection patterns."""
    return any(re.search(p, content) for p in INJECTION_PATTERNS)


class AccessControl:
    def __init__(self, policy_path: str = "./storage/rbac_policy.json"):
        self.policy_path = policy_path
        self.permissions = {}
        self._load_policy()

    def _load_policy(self):
        if os.path.exists(self.policy_path):
            try:
                with open(self.policy_path, "r") as f:
                    self.permissions = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load RBAC policy from {self.policy_path}: {e}")
                self.permissions = {"system": {"system": "WRITE"}}
                self._save_policy()
        else:
            self.permissions = {"system": {"system": "WRITE"}}
            self._save_policy()

    def _save_policy(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.policy_path)), exist_ok=True)
        try:
            with open(self.policy_path, "w") as f:
                json.dump(self.permissions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save RBAC policy to {self.policy_path}: {e}")

    def grant_access(self, agent_id: str, session_id: str, level: str):
        if level not in ("READ", "WRITE"):
            raise ValueError(f"Invalid access level: {level}. Must be 'READ' or 'WRITE'.")
        if agent_id not in self.permissions:
            self.permissions[agent_id] = {}
        self.permissions[agent_id][session_id] = level
        self._save_policy()

    def revoke_access(self, agent_id: str, session_id: str):
        if agent_id in self.permissions and session_id in self.permissions[agent_id]:
            del self.permissions[agent_id][session_id]
            if not self.permissions[agent_id]:
                del self.permissions[agent_id]
            self._save_policy()

    def check_access(self, agent_id: str, session_id: str, required_level: str) -> bool:
        if agent_id not in self.permissions:
            return False
        if session_id not in self.permissions[agent_id]:
            return False
        granted = self.permissions[agent_id][session_id]
        if required_level == "READ":
            return granted in ("READ", "WRITE")
        if required_level == "WRITE":
            return granted == "WRITE"
        return False


def sanitize_cmb_content(content: str) -> str:
    content = content.replace("\x00", "")
    # Strip dangerous tags AND their content (script, style, etc.)
    content = re.sub(r"<(script|style|iframe|object|embed)[^>]*>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining HTML tags
    content = re.sub(r"<[^>]*>", "", content)
    content = " ".join(content.split())

    # Prompt injection detection — reject adversarial content before it
    # enters the memory pipeline and gets interpolated into LLM prompts.
    if detect_prompt_injection(content):
        logger.warning(f"PROMPT_INJECTION_DETECTED: Content rejected by security layer")
        raise PromptInjectionError(
            "PROMPT_INJECTION_DETECTED: Content contains adversarial patterns "
            "and has been rejected by the security layer."
        )

    return content
