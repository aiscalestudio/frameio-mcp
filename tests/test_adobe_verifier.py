"""Tests for Adobe IMS token verification.

The behaviour that matters most here is failing closed. This verifier stands between an
anonymous caller and someone's Frame.io account, so every ambiguous outcome (Adobe
unreachable, malformed response, unexpected status) has to deny rather than allow.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
import respx

from frameio_mcp.adobe_verifier import (
    ADOBE_IMS_VALIDATE_URL,
    AdobeIMSTokenVerifier,
    _extract_scopes,
)

CLIENT_ID = "12cc3b73f23948f4b4d69cdef0b96602"
ALL_SCOPES = "openid,AdobeID,email,profile,offline_access,additional_info.roles"


def adobe_token(scope: str = ALL_SCOPES, created_at: int | None = None) -> str:
    """Build a token shaped like a real Adobe access token.

    Includes the `x5u` header that makes these tokens unverifiable by a conforming JWT
    library, so the fixtures reflect the actual problem rather than an idealised token.
    """
    header = {"alg": "RS256", "x5u": "ims_na1-key-at-1.cer", "kid": "ims_na1-key-at-1"}
    payload = {
        "type": "access_token",
        "client_id": CLIENT_ID,
        "user_id": "0E3521CE69309E750A495C06@AdobeID",
        "scope": scope,
        "created_at": str(created_at if created_at else int(time.time() * 1000)),
        "expires_in": "3600000",
    }

    def seg(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{seg(header)}.{seg(payload)}.signature-not-checked-locally"


def mock_adobe(payload: dict, status: int = 200):
    return respx.post(ADOBE_IMS_VALIDATE_URL).mock(
        return_value=httpx.Response(status, json=payload)
    )


@pytest.fixture
def verifier() -> AdobeIMSTokenVerifier:
    return AdobeIMSTokenVerifier(
        client_id=CLIENT_ID, required_scopes=ALL_SCOPES.split(",")
    )


class TestExtractScopes:
    def test_handles_adobe_comma_separated_scopes(self):
        """Adobe uses commas where RFC 6749 uses spaces."""
        assert _extract_scopes({"scope": "openid,email"}) == ["openid", "email"]

    def test_handles_space_separated_scopes(self):
        assert _extract_scopes({"scope": "openid email"}) == ["openid", "email"]

    def test_missing_scope_claim_yields_empty(self):
        assert _extract_scopes({}) == []


class TestValidToken:
    @respx.mock
    async def test_accepts_a_token_adobe_confirms(self, verifier):
        mock_adobe(
            {
                "valid": True,
                "token": {"client_id": CLIENT_ID, "user_id": "user@AdobeID"},
            }
        )

        result = await verifier.verify_token(adobe_token())

        assert result is not None
        assert result.client_id == CLIENT_ID
        assert result.subject == "user@AdobeID"
        assert "additional_info.roles" in result.scopes

    @respx.mock
    async def test_returns_the_original_token_for_frameio(self, verifier):
        """Tools send this value straight to Frame.io, so it must not be substituted."""
        token = adobe_token()
        mock_adobe({"valid": True, "token": {"client_id": CLIENT_ID}})

        result = await verifier.verify_token(token)

        assert result is not None
        assert result.token == token

    @respx.mock
    async def test_derives_expiry_from_adobe_millisecond_claims(self, verifier):
        """Adobe has no `exp`; it uses created_at + expires_in in milliseconds."""
        created_ms = 1_785_350_322_971
        mock_adobe({"valid": True, "token": {"client_id": CLIENT_ID}})

        result = await verifier.verify_token(adobe_token(created_at=created_ms))

        assert result is not None
        assert result.expires_at == int((created_ms + 3_600_000) / 1000)

    @respx.mock
    async def test_sends_the_parameters_adobe_expects(self, verifier):
        route = mock_adobe({"valid": True, "token": {}})
        token = adobe_token()

        await verifier.verify_token(token)

        body = route.calls.last.request.content.decode()
        assert f"client_id={CLIENT_ID}" in body
        assert "type=access_token" in body
        assert token.split(".")[0] in body


class TestRejection:
    @respx.mock
    async def test_rejects_when_adobe_says_invalid(self, verifier):
        mock_adobe({"valid": False, "reason": "bad_signature"})
        assert await verifier.verify_token(adobe_token()) is None

    @respx.mock
    async def test_rejects_when_adobe_says_expired(self, verifier):
        mock_adobe({"valid": False, "reason": "expired_token"})
        assert await verifier.verify_token(adobe_token()) is None

    async def test_rejects_an_empty_token_without_calling_adobe(self, verifier):
        assert await verifier.verify_token("") is None

    @respx.mock
    async def test_rejects_a_valid_token_missing_required_scopes(self, verifier):
        """A token can be genuinely valid and still not carry what Frame.io v4 needs."""
        mock_adobe({"valid": True, "token": {"client_id": CLIENT_ID}})

        result = await verifier.verify_token(adobe_token(scope="openid,email"))

        assert result is None


class TestFailsClosed:
    """Every ambiguous outcome must deny. This verifier guards Frame.io accounts."""

    @respx.mock
    async def test_network_failure_denies(self, verifier):
        respx.post(ADOBE_IMS_VALIDATE_URL).mock(
            side_effect=httpx.ConnectError("adobe unreachable")
        )
        assert await verifier.verify_token(adobe_token()) is None

    @respx.mock
    async def test_timeout_denies(self, verifier):
        respx.post(ADOBE_IMS_VALIDATE_URL).mock(
            side_effect=httpx.ReadTimeout("too slow")
        )
        assert await verifier.verify_token(adobe_token()) is None

    @respx.mock
    async def test_server_error_denies(self, verifier):
        mock_adobe({"error": "internal"}, status=500)
        assert await verifier.verify_token(adobe_token()) is None

    @respx.mock
    async def test_non_json_response_denies(self, verifier):
        respx.post(ADOBE_IMS_VALIDATE_URL).mock(
            return_value=httpx.Response(200, text="<html>maintenance</html>")
        )
        assert await verifier.verify_token(adobe_token()) is None

    @respx.mock
    async def test_response_without_valid_field_denies(self, verifier):
        mock_adobe({"token": {"client_id": CLIENT_ID}})
        assert await verifier.verify_token(adobe_token()) is None
