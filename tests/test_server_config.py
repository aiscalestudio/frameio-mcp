"""Tests for the hosted server's configuration.

The hosted server has a different failure mode from the local CLI: a missing or
process-local value does not fail loudly at startup, it fails intermittently in
production as users get silently signed out. These tests exist to make those
requirements explicit and enforced.
"""

from __future__ import annotations

import pytest

from frameio_mcp.server_config import ServerConfig


@pytest.fixture
def full_env(monkeypatch):
    monkeypatch.setenv("FRAMEIO_CLIENT_ID", "client-abc")
    monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("FRAMEIO_BASE_URL", "https://frameio-mcp.vercel.app")
    monkeypatch.setenv("JWT_SIGNING_KEY", "a-fixed-signing-key")
    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", "a-fernet-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")


class TestFromEnv:
    def test_reads_every_setting(self, full_env):
        c = ServerConfig.from_env()
        assert c.client_id == "client-abc"
        assert c.client_secret == "secret-xyz"
        assert c.base_url == "https://frameio-mcp.vercel.app"
        assert c.jwt_signing_key == "a-fixed-signing-key"
        assert c.storage_encryption_key == "a-fernet-key"
        assert c.redis_url == "redis://localhost:6379"

    @pytest.mark.parametrize(
        "missing",
        [
            "FRAMEIO_CLIENT_ID",
            "FRAMEIO_CLIENT_SECRET",
            "FRAMEIO_BASE_URL",
            "JWT_SIGNING_KEY",
            "STORAGE_ENCRYPTION_KEY",
            "REDIS_URL",
        ],
    )
    def test_every_setting_is_required(self, full_env, monkeypatch, missing):
        """None of these have a safe default.

        A derived JWT signing key or an in-process store works perfectly on one
        machine and breaks silently the moment a second instance exists.
        """
        monkeypatch.delenv(missing, raising=False)
        with pytest.raises(EnvironmentError, match=missing):
            ServerConfig.from_env()

    def test_error_names_every_missing_variable_at_once(self, full_env, monkeypatch):
        """Reporting one at a time turns setup into six deploy cycles."""
        monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        with pytest.raises(EnvironmentError) as exc:
            ServerConfig.from_env()
        assert "JWT_SIGNING_KEY" in str(exc.value)
        assert "REDIS_URL" in str(exc.value)


class TestBaseUrl:
    def test_trailing_slash_is_stripped(self, full_env, monkeypatch):
        """The redirect URI is built from this and must match Adobe exactly."""
        monkeypatch.setenv("FRAMEIO_BASE_URL", "https://frameio-mcp.vercel.app/")
        assert ServerConfig.from_env().base_url == "https://frameio-mcp.vercel.app"

    def test_plain_http_is_rejected(self, full_env, monkeypatch):
        """OAuth codes and bearer tokens must not travel unencrypted."""
        monkeypatch.setenv("FRAMEIO_BASE_URL", "http://frameio-mcp.vercel.app")
        with pytest.raises(EnvironmentError, match="https"):
            ServerConfig.from_env()

    def test_localhost_over_http_is_allowed(self, full_env, monkeypatch):
        """Local development has no TLS; the exemption is deliberate and narrow."""
        monkeypatch.setenv("FRAMEIO_BASE_URL", "http://localhost:8000")
        assert ServerConfig.from_env().base_url == "http://localhost:8000"


class TestDerivedValues:
    def test_redirect_uri_is_derived_from_base_url(self, full_env):
        c = ServerConfig.from_env()
        assert c.redirect_uri == "https://frameio-mcp.vercel.app/auth/callback"

    def test_scopes_match_the_verified_v4_requirement(self, full_env):
        """These are the scopes proven to work against live Frame.io v4."""
        assert set(ServerConfig.from_env().required_scopes) == {
            "openid",
            "AdobeID",
            "email",
            "profile",
            "offline_access",
            "additional_info.roles",
        }
