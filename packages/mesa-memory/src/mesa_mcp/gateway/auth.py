"""Authentication and authorization for the HTTP Gateway."""

from dataclasses import dataclass

from mesa_storage.control.credential_repo import CredentialRepository


@dataclass(frozen=True)
class GatewayPrincipal:
    """Immutable authorization scope carried by a Codex MCP session."""

    client_id: str
    credential_id: str
    binding_id: str


class GatewayAuth:
    def __init__(self, credential_repo: CredentialRepository | None = None):
        self.credential_repo = credential_repo

    async def authenticate_credential(self, token: str) -> GatewayPrincipal | None:
        """Resolve a direct HTTP credential to its one durable project binding."""
        if self.credential_repo is None:
            return None
        credential = await self.credential_repo.authenticate(token)
        if credential is None:
            return None
        return GatewayPrincipal(
            client_id=str(credential["client_id"]),
            credential_id=str(credential["credential_id"]),
            binding_id=str(credential["binding_id"]),
        )
