"""Bearer-token verification for MCP servers.

Each MCP server is an OAuth 2.1 protected resource (as the MCP spec requires
for HTTP transports). In this deployment the agent service presents a static
per-server token from a Kubernetes Secret; swapping in JWT validation against
a real issuer only means another ``TokenVerifier`` implementation.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence

from mcp.server.auth.provider import AccessToken


class StaticTokenVerifier:
    """Accepts exactly one shared secret and grants a fixed set of scopes."""

    def __init__(self, secret: str, scopes: Sequence[str], resource: str, client_id: str = "agent-service"):
        if not secret:
            raise ValueError("MCP_TOKEN must be set: refusing to run an MCP server without authentication")
        self._secret = secret.encode()
        self._scopes = list(scopes)
        self._resource = resource
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        # Constant-time comparison: no timing side channel on the secret.
        if not hmac.compare_digest(token.encode(), self._secret):
            return None
        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=self._scopes,
            resource=self._resource,
        )
