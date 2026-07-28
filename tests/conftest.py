"""Shared pytest fixtures."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from frameio_mcp.auth import Tokens
from frameio_mcp.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """A test config with fake credentials + a tmp tokens path."""
    return Config(
        client_id="test-client-id",
        client_secret="test-client-secret",
        oauth_relay_url="https://aiscalestudio.github.io/frameio-mcp/callback.html",
        tokens_path=tmp_path / "tokens.json",
        scopes="openid,AdobeID,offline_access",
    )


@pytest.fixture
def valid_tokens() -> Tokens:
    """Tokens that are valid for the next hour."""
    return Tokens(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        expires_at=time.time() + 3600,
        account_id="acct-1",
    )


@pytest.fixture
def expired_tokens() -> Tokens:
    """Tokens that expired an hour ago."""
    return Tokens(
        access_token="access-old",
        refresh_token="refresh-still-good",
        expires_at=time.time() - 3600,
        account_id="acct-1",
    )
