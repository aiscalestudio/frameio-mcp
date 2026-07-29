"""Tests for env-based config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from frameio_mcp.config import DEFAULT_SCOPES, REQUIRED_V4_SCOPES, Config


def test_default_scopes_include_everything_frameio_v4_needs():
    """Frame.io v4 rejects IMS tokens that lack role claims.

    v2 endpoints accept a bare `openid` token, so a missing scope shows up only as a
    blanket 401 on every v4 call. Adobe requires `additional_info.roles` plus `email`
    and `profile` for v4 authorization to resolve.
    """
    granted = {s.strip() for s in DEFAULT_SCOPES.split(",")}
    assert granted >= REQUIRED_V4_SCOPES, (
        f"Missing v4 scopes: {sorted(REQUIRED_V4_SCOPES - granted)}"
    )


def test_default_scopes_have_no_stray_whitespace():
    """Adobe IMS takes a comma-separated list; stray spaces break the authorize URL."""
    assert " " not in DEFAULT_SCOPES
    assert not DEFAULT_SCOPES.startswith(",")
    assert not DEFAULT_SCOPES.endswith(",")


def test_config_scopes_default_to_module_default():
    c = Config(
        client_id="c",
        client_secret="s",
        oauth_relay_url="https://example.invalid/cb",
        tokens_path=Path("/tmp/t.json"),
    )
    assert c.scopes == DEFAULT_SCOPES


def test_from_env_reads_required_vars(monkeypatch):
    monkeypatch.setenv("FRAMEIO_CLIENT_ID", "my-client")
    monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "my-secret")
    c = Config.from_env()
    assert c.client_id == "my-client"
    assert c.client_secret == "my-secret"


def test_from_env_missing_client_id_raises(monkeypatch):
    monkeypatch.delenv("FRAMEIO_CLIENT_ID", raising=False)
    monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "s")
    with pytest.raises(EnvironmentError, match="FRAMEIO_CLIENT_ID"):
        Config.from_env()


def test_from_env_missing_client_secret_raises(monkeypatch):
    monkeypatch.setenv("FRAMEIO_CLIENT_ID", "c")
    monkeypatch.delenv("FRAMEIO_CLIENT_SECRET", raising=False)
    with pytest.raises(EnvironmentError, match="FRAMEIO_CLIENT_SECRET"):
        Config.from_env()


def test_from_env_defaults_for_optional_vars(monkeypatch):
    monkeypatch.setenv("FRAMEIO_CLIENT_ID", "c")
    monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "s")
    monkeypatch.delenv("FRAMEIO_OAUTH_RELAY_URL", raising=False)
    monkeypatch.delenv("FRAMEIO_TOKENS_PATH", raising=False)
    c = Config.from_env()
    assert "aiscalestudio.github.io" in c.oauth_relay_url
    assert c.tokens_path.name == "tokens.json"


def test_custom_tokens_path(monkeypatch, tmp_path):
    monkeypatch.setenv("FRAMEIO_CLIENT_ID", "c")
    monkeypatch.setenv("FRAMEIO_CLIENT_SECRET", "s")
    custom = tmp_path / "custom-tokens.json"
    monkeypatch.setenv("FRAMEIO_TOKENS_PATH", str(custom))
    c = Config.from_env()
    assert c.tokens_path == custom
