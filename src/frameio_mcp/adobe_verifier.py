"""Token verification for Adobe IMS via Adobe's own introspection endpoint.

Adobe IMS tokens cannot be verified as JWTs. They are JWT-shaped, and their signing
keys are published in the JWKS at /ims/keys, but every token header carries:

    "x5u": "ims_na1-key-at-1.cer"

RFC 7515 requires `x5u` to be a URI. Adobe puts a bare filename there, so a conforming
JWT library rejects the token on header validation before it ever checks the signature:

    InvalidHeaderValueError: 'x5u' in header must be a URL

Both the access token and the id_token carry it, so switching which one is verified does
not help. The symptom is a server that authenticates a user against Adobe and then
rejects the credentials it just issued.

So verification goes to Adobe instead of being reimplemented locally. Adobe's
`/ims/validate_token/v1` answers `{"valid": true, "token": {...}}` or
`{"valid": false, "reason": "..."}`. That is authoritative, and unlike local signature
checking it also reflects revocation.

Cost is one HTTP round trip per MCP request. Acceptable here: the request already reads
OAuth state from Redis, and correctness beats shaving a hop.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

import httpx
from fastmcp.server.auth.auth import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)

ADOBE_IMS_VALIDATE_URL = "https://ims-na1.adobelogin.com/ims/validate_token/v1"

VALIDATION_TIMEOUT_SECONDS = 15.0


def _decode_unverified_claims(token: str) -> dict[str, Any]:
    """Read a token's payload without verifying it.

    Only ever called after Adobe has confirmed the token is valid, so this is reading
    already-trusted data rather than making a security decision. It exists because the
    introspection response does not include the granted scopes, and those are needed to
    enforce `required_scopes`.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # base64url in JWTs is unpadded
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}


def _extract_scopes(claims: dict[str, Any]) -> list[str]:
    """Pull the granted scopes out of an Adobe token.

    Adobe returns them comma-separated, unlike the space-separated form in RFC 6749,
    so both separators are handled.
    """
    raw = claims.get("scope") or ""
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return [s.strip() for s in str(raw).replace(" ", ",").split(",") if s.strip()]


class AdobeIMSTokenVerifier(TokenVerifier):
    """Verifies Adobe IMS access tokens through Adobe's introspection endpoint."""

    def __init__(
        self,
        client_id: str,
        required_scopes: list[str] | None = None,
        base_url: str | None = None,
        validate_url: str = ADOBE_IMS_VALIDATE_URL,
    ):
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self.client_id = client_id
        self.validate_url = validate_url

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an AccessToken if Adobe says the token is valid, else None."""
        if not token:
            return None

        introspection = await self._introspect(token)
        if introspection is None:
            return None

        if not introspection.get("valid"):
            logger.info(
                "Adobe rejected the token: %s",
                introspection.get("reason", "no reason given"),
            )
            return None

        details = introspection.get("token") or {}
        claims = _decode_unverified_claims(token)
        scopes = _extract_scopes(claims)

        if self.required_scopes:
            missing = set(self.required_scopes) - set(scopes)
            if missing:
                logger.warning(
                    "Token is valid but missing required scopes: %s",
                    ", ".join(sorted(missing)),
                )
                return None

        return AccessToken(
            token=token,
            client_id=str(details.get("client_id") or self.client_id),
            scopes=scopes,
            expires_at=self._expires_at(claims),
            subject=str(details.get("user_id")) if details.get("user_id") else None,
            claims={**claims, "adobe_introspection": details},
        )

    async def _introspect(self, token: str) -> dict[str, Any] | None:
        """Ask Adobe whether the token is valid. Returns None if Adobe is unreachable."""
        try:
            async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self.validate_url,
                    data={
                        "client_id": self.client_id,
                        "token": token,
                        "type": "access_token",
                    },
                )
        except httpx.HTTPError as e:
            # Fail closed. A network problem must not become an auth bypass.
            logger.error("Could not reach Adobe token validation: %s", e)
            return None

        if response.status_code != 200:
            logger.error(
                "Adobe token validation returned %s: %s",
                response.status_code,
                response.text[:200],
            )
            return None

        try:
            return response.json()
        except ValueError:
            logger.error("Adobe token validation returned non-JSON")
            return None

    @staticmethod
    def _expires_at(claims: dict[str, Any]) -> int | None:
        """Work out expiry from Adobe's non-standard claims.

        Adobe access tokens have no `exp`. They carry `created_at` and `expires_in` as
        millisecond strings instead.
        """
        created_at = claims.get("created_at")
        expires_in = claims.get("expires_in")
        if created_at is None or expires_in is None:
            return None
        try:
            return int((int(created_at) + int(expires_in)) / 1000)
        except (TypeError, ValueError):
            return None
