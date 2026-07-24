"""Authentication and authorization for the HTTP Gateway."""

from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mesa_storage.control.client_repo import ClientRepository

security = HTTPBearer(auto_error=False)


class GatewayAuth:
    def __init__(self, client_repo: ClientRepository):
        self.client_repo = client_repo

    async def authenticate(
        self, credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
    ) -> str:
        """Verify the Bearer token against registered clients and return client_id."""
        if not credentials:
            raise HTTPException(
                status_code=401, detail="Missing or invalid authentication token"
            )

        token = credentials.credentials

        # Currently, the DB stores clients but we might need to verify their keys.
        # For this MVP, we assume the token IS the client_id or api_key.
        # In a real scenario, we'd verify a signed JWT or a hashed secret.

        client = await self.client_repo.get_client(token)
        if not client:
            raise HTTPException(status_code=401, detail="Invalid client identity")

        if not client.get("enabled", True):
            raise HTTPException(status_code=403, detail="Client is disabled")

        return client["client_id"]
