"""Approval tokens: the platform's proof that a human approved one exact action.

When an approver clicks Approve, the platform signs a short-lived JWT (RS256,
its own key, published at /.well-known/jwks.json) whose ``action_sha256``
claim is the SHA-256 of the action's exact JSON text, the text that was shown
to the approver. This server runs a write only if the token verifies and the
hash matches the action it was asked to run. So neither the agent nor anything
that injects text into it can approve its own writes, or swap the action after
approval.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

AUDIENCE = "relay-actions"


class ApprovalError(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    id: str
    approved_by: str
    incident: str


def action_digest(action: str) -> str:
    return hashlib.sha256(action.encode()).hexdigest()


class ApprovalVerifier:
    def __init__(self, signing_key: Callable[[str], Any], issuer: str, audience: str = AUDIENCE):
        """``signing_key(token)`` returns the public key that should have signed it."""
        self._signing_key = signing_key
        self.issuer = issuer
        self.audience = audience

    @classmethod
    def from_jwks(cls, jwks_url: str, issuer: str) -> ApprovalVerifier:
        client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        return cls(lambda token: client.get_signing_key_from_jwt(token).key, issuer)

    def verify(self, token: str, action: str) -> Approval:
        try:
            claims = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "action_sha256"]},
            )
        except (jwt.PyJWTError, ValueError) as e:
            raise ApprovalError(f"approval token rejected: {e}") from e
        if claims["action_sha256"] != action_digest(action):
            raise ApprovalError("the approval is for a different action")
        return Approval(claims["sub"], str(claims.get("approved_by", "")), str(claims.get("incident", "")))
