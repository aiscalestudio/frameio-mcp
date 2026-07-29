"""Tests for the hosted ASGI application.

These assert the properties that Cowork depends on and that fail silently rather than
loudly when wrong: the OAuth metadata Claude discovers, self-registration, and the fact
that an unauthenticated caller cannot reach the tools.

Adobe's discovery document is fetched for real when OIDCProxy is constructed, so these
are marked `network`.
"""

from __future__ import annotations

import pytest
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from frameio_mcp.app import build_auth, create_app
from frameio_mcp.server_config import ServerConfig

pytestmark = pytest.mark.network

BASE_URL = "https://frameio-mcp.example.com"


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        client_id="test-client-id",
        client_secret="test-client-secret",
        base_url=BASE_URL,
        jwt_signing_key="fixed-signing-key-for-tests",
        storage_encryption_key="unused-when-storage-is-injected",
        redis_url="redis://unused",
    )


@pytest.fixture
def client(config) -> TestClient:
    # MemoryStore only because these tests never restart the app. Production must use
    # the Redis-backed store; see spike/FINDINGS.md for why.
    app = create_app(config, client_storage=MemoryStore())
    with TestClient(app) as test_client:
        yield test_client


class TestAuthWiring:
    def test_uses_adobe_ims_discovery(self, config):
        auth = build_auth(config, client_storage=MemoryStore())
        assert "adobelogin.com" in str(auth.oidc_config.token_endpoint)

    def test_requests_the_verified_v4_scopes(self, config):
        """Dropping additional_info.roles here reintroduces the blanket v4 401."""
        auth = build_auth(config, client_storage=MemoryStore())
        assert "additional_info.roles" in auth.required_scopes


class TestOAuthMetadata:
    def test_publishes_authorization_server_metadata(self, client):
        body = client.get("/.well-known/oauth-authorization-server").json()
        assert body["authorization_endpoint"].startswith(BASE_URL)
        assert body["token_endpoint"].startswith(BASE_URL)

    def test_advertises_dynamic_client_registration(self, client):
        """Without this, a Cowork user has to paste an OAuth client ID and secret."""
        body = client.get("/.well-known/oauth-authorization-server").json()
        assert body.get("registration_endpoint"), "DCR endpoint missing"

    def test_publishes_protected_resource_metadata(self, client):
        body = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert body["authorization_servers"]


class TestAccessControl:
    def test_unauthenticated_tool_call_is_rejected(self, client):
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 401

    def test_rejection_points_the_client_at_the_metadata(self, client):
        """This header is how Claude discovers where to authenticate."""
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert "resource_metadata" in response.headers.get("www-authenticate", "")

    def test_garbage_bearer_token_is_rejected(self, client):
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer not-a-real-token",
            },
        )
        assert response.status_code == 401


class TestClientRegistration:
    def test_a_client_can_register_itself(self, client):
        response = client.post(
            "/register",
            json={
                "client_name": "test-harness",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert response.status_code in (200, 201), response.text
        assert response.json().get("client_id")
