import logging
from typing import Any

from mesa_storage.control.policy_repo import PolicyRepository
from mesa_storage.control.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)


class PolicyEngine:
    def __init__(
        self, policy_repo: PolicyRepository, settings_repo: SettingsRepository
    ):
        self.policy_repo = policy_repo
        self.settings_repo = settings_repo

    async def evaluate(
        self,
        client_id: str,
        project_id: str | None,
        operation: str,
        **context_kwargs: Any,
    ) -> str:
        """
        Returns effect: ALLOW, DENY, REQUIRE_APPROVAL
        Evaluation priority:
        1. Explicit deny rules (priority > default)
        2. Client/project specific rules
        3. Global rules
        4. Global default from settings
        5. Hardcoded safest default
        """
        rules = await self.policy_repo.list_rules(operation=operation)

        matching_rules = []
        for r in rules:
            scope = r["scope_type"]
            scope_id = r["scope_id"]
            if scope == "GLOBAL":
                matching_rules.append(r)
            elif scope == "CLIENT" and scope_id == client_id:
                matching_rules.append(r)
            elif scope == "PROJECT" and scope_id == project_id:
                matching_rules.append(r)

        if matching_rules:
            # Sort by priority DESC, then by rule_id for stability
            matching_rules.sort(
                key=lambda x: (x["priority"], x["rule_id"]), reverse=True
            )
            return matching_rules[0]["effect"]

        # Default policy from settings if applicable
        setting_key = None
        if operation in ("WRITE", "UPDATE"):
            setting_key = "writes.default_policy"
        elif operation == "DELETE":
            setting_key = "deletes.default_policy"

        if setting_key:
            default_val = await self.settings_repo.get_setting(setting_key)
            if default_val:
                return default_val

        # Hardcoded Safest default
        safest_defaults = {
            "HEALTH": "ALLOW",
            "READ": "ALLOW",
            "SEARCH": "ALLOW",
            "CONTEXT": "ALLOW",
            "WRITE": "REQUIRE_APPROVAL",
            "UPDATE": "REQUIRE_APPROVAL",
            "DELETE": "REQUIRE_APPROVAL",
            "REPLAY": "REQUIRE_APPROVAL",
            "ROLLBACK": "DENY",
            "GRAPH_PROCESS": "ALLOW",
            "VECTOR_INDEX": "ALLOW",
            "VIEW_RAW_CONTENT": "REQUIRE_APPROVAL",
        }
        return safest_defaults.get(operation, "DENY")
