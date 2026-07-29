"""Tests for the Frame.io v4 entitlement diagnostics."""

from __future__ import annotations

import base64
import json
import time

import httpx
import respx

from frameio_mcp.auth import Tokens
from frameio_mcp.config import FRAMEIO_API_BASE_URL
from frameio_mcp.diagnostics import (
    check_granted_scopes,
    decode_token_scopes,
    run_entitlement_checks,
)


def make_jwt(scope: str | list[str]) -> str:
    """Build an unsigned JWT-shaped token carrying a `scope` claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    payload_bytes = json.dumps({"scope": scope}).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    return f"{header}.{payload}.signature-not-verified"


def tokens_with(scope: str | list[str]) -> Tokens:
    return Tokens(
        access_token=make_jwt(scope),
        refresh_token="refresh-xyz",
        expires_at=time.time() + 3600,
    )


ALL_SCOPES = "openid,email,profile,offline_access,additional_info.roles"


class TestDecodeTokenScopes:
    def test_comma_separated_scope_claim(self):
        assert decode_token_scopes(make_jwt("openid,email")) == {"openid", "email"}

    def test_space_separated_scope_claim(self):
        assert decode_token_scopes(make_jwt("openid email")) == {"openid", "email"}

    def test_list_scope_claim(self):
        assert decode_token_scopes(make_jwt(["openid", "email"])) == {"openid", "email"}

    def test_payload_without_padding_still_decodes(self):
        """base64url in JWTs is unpadded; the decoder must restore it."""
        scopes = decode_token_scopes(make_jwt("openid,email,profile,offline_access"))
        assert "offline_access" in scopes

    def test_opaque_token_returns_empty_set(self):
        assert decode_token_scopes("not-a-jwt") == set()

    def test_garbage_payload_returns_empty_set(self):
        assert decode_token_scopes("aaa.!!!not-base64!!!.ccc") == set()


class TestCheckGrantedScopes:
    def test_passes_when_all_required_scopes_present(self):
        result = check_granted_scopes(tokens_with(ALL_SCOPES))
        assert result.passed

    def test_fails_and_names_the_missing_scope(self):
        """The old default omitted additional_info.roles, which is the silent killer."""
        result = check_granted_scopes(tokens_with("openid,AdobeID,offline_access"))
        assert not result.passed
        assert "additional_info.roles" in result.detail
        assert result.remedy is not None

    def test_opaque_token_does_not_fail_the_check(self):
        """An unreadable token is not evidence of a missing scope; let live calls decide."""
        tokens = Tokens(
            access_token="opaque", refresh_token="r", expires_at=time.time() + 3600
        )
        assert check_granted_scopes(tokens).passed


class TestRunEntitlementChecks:
    @respx.mock
    async def test_stops_at_identity_failure(self):
        """A 401 on /me makes every later check a duplicate of the same cause."""
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(401, json={"errors": ["not authorized"]})
        )

        results = await run_entitlement_checks(tokens_with(ALL_SCOPES))

        assert [r.name for r in results] == ["Granted scopes", "v4 identity (GET /me)"]
        assert not results[-1].passed
        assert "product profile" in results[-1].remedy

    @respx.mock
    async def test_happy_path_through_accounts(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"email": "dan@example.com"}})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "acct-1", "name": "AI Scale Studio"}]}
            )
        )

        results = await run_entitlement_checks(tokens_with(ALL_SCOPES))

        assert all(r.passed for r in results)
        assert "dan@example.com" in results[1].detail
        assert "AI Scale Studio" in results[2].detail

    @respx.mock
    async def test_zero_accounts_is_a_failure(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "u1"}})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        results = await run_entitlement_checks(tokens_with(ALL_SCOPES))

        assert not results[-1].passed
        assert "no Frame.io accounts" in results[-1].remedy

    @respx.mock
    async def test_write_probe_runs_only_when_text_supplied(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "u1"}})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "acct-1"}]})
        )
        respx.get(
            f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
        ).mock(return_value=httpx.Response(200, json={"data": []}))

        results = await run_entitlement_checks(
            tokens_with(ALL_SCOPES), file_id="file-1"
        )

        assert [r.name for r in results][-1] == "v4 read (list comments)"

    @respx.mock
    async def test_write_probe_reports_created_comment(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "u1"}})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "acct-1"}]})
        )
        respx.get(
            f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
        ).mock(return_value=httpx.Response(200, json={"data": []}))
        respx.post(
            f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
        ).mock(return_value=httpx.Response(200, json={"data": {"id": "cmt-9"}}))

        results = await run_entitlement_checks(
            tokens_with(ALL_SCOPES),
            file_id="file-1",
            write_probe_text="probe",
        )

        assert results[-1].passed
        assert results[-1].data["comment_id"] == "cmt-9"

    @respx.mock
    async def test_write_failure_distinguishes_role_problem_from_oauth(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "u1"}})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "acct-1"}]})
        )
        respx.get(
            f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
        ).mock(return_value=httpx.Response(200, json={"data": []}))
        respx.post(
            f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
        ).mock(return_value=httpx.Response(403, json={"errors": ["forbidden"]}))

        results = await run_entitlement_checks(
            tokens_with(ALL_SCOPES),
            file_id="file-1",
            write_probe_text="probe",
        )

        assert not results[-1].passed
        assert "comment permission" in results[-1].remedy
