"""Tests for OAuth flow, token storage, refresh."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from frameio_mcp.auth import (
    AuthError,
    Tokens,
    build_authorize_url,
    clear_tokens,
    exchange_code_for_tokens,
    generate_state,
    load_tokens,
    refresh_tokens,
    save_tokens,
)
from frameio_mcp.config import ADOBE_IMS_TOKEN_URL


def test_generate_state_is_random_and_url_safe():
    a = generate_state()
    b = generate_state()
    assert a != b
    assert len(a) >= 32
    # URL-safe base64 chars only
    assert all(c.isalnum() or c in "-_" for c in a)


def test_build_authorize_url_includes_required_params(config):
    state = "abc123"
    url = build_authorize_url(config, state)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "ims-na1.adobelogin.com"
    assert qs["client_id"] == [config.client_id]
    assert qs["scope"] == [config.scopes]
    assert qs["response_type"] == ["code"]
    assert qs["redirect_uri"] == [config.oauth_relay_url]
    assert qs["state"] == [state]


def test_tokens_is_expired_true_when_past_expires_at():
    t = Tokens(
        access_token="a", refresh_token="r", expires_at=time.time() - 100
    )
    assert t.is_expired is True


def test_tokens_is_expired_false_when_future(valid_tokens):
    assert valid_tokens.is_expired is False


def test_tokens_is_expired_refreshes_60s_early():
    t = Tokens(
        access_token="a", refresh_token="r", expires_at=time.time() + 30
    )
    # 30s from now is within the 60s refresh buffer → treated as expired
    assert t.is_expired is True


def test_tokens_roundtrip_via_dict(valid_tokens):
    d = valid_tokens.to_dict()
    reconstructed = Tokens.from_dict(d)
    assert reconstructed.access_token == valid_tokens.access_token
    assert reconstructed.refresh_token == valid_tokens.refresh_token
    assert reconstructed.expires_at == valid_tokens.expires_at
    assert reconstructed.account_id == valid_tokens.account_id


def test_save_tokens_writes_file_with_0600(config, valid_tokens):
    save_tokens(valid_tokens, config.tokens_path)
    assert config.tokens_path.exists()
    # On Unix, check mode; skip on Windows
    import os
    if os.name == "posix":
        mode = config.tokens_path.stat().st_mode & 0o777
        assert mode == 0o600


def test_load_tokens_returns_none_if_no_file(config):
    assert load_tokens(config.tokens_path) is None


def test_load_tokens_reads_saved_file(config, valid_tokens):
    save_tokens(valid_tokens, config.tokens_path)
    loaded = load_tokens(config.tokens_path)
    assert loaded is not None
    assert loaded.access_token == valid_tokens.access_token


def test_load_tokens_raises_on_corrupted_json(config):
    config.tokens_path.parent.mkdir(parents=True, exist_ok=True)
    config.tokens_path.write_text("not json")
    with pytest.raises(AuthError, match="Corrupted tokens file"):
        load_tokens(config.tokens_path)


def test_clear_tokens_removes_file(config, valid_tokens):
    save_tokens(valid_tokens, config.tokens_path)
    assert clear_tokens(config.tokens_path) is True
    assert not config.tokens_path.exists()


def test_clear_tokens_returns_false_if_no_file(config):
    assert clear_tokens(config.tokens_path) is False


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_success(config):
    respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 86400,
                "token_type": "Bearer",
            },
        )
    )
    tokens = await exchange_code_for_tokens(config, "auth-code-xyz")
    assert tokens.access_token == "at-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.expires_at > time.time() + 86000  # ~86400s from now


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_raises_on_error(config):
    respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(400, text="invalid_grant")
    )
    with pytest.raises(AuthError, match="Token exchange failed"):
        await exchange_code_for_tokens(config, "bad-code")


@pytest.mark.asyncio
@respx.mock
async def test_refresh_tokens_uses_new_refresh_token_if_returned(config, expired_tokens):
    respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-2",
                "refresh_token": "rt-2",
                "expires_in": 86400,
            },
        )
    )
    new_tokens = await refresh_tokens(config, expired_tokens)
    assert new_tokens.access_token == "at-2"
    assert new_tokens.refresh_token == "rt-2"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_tokens_falls_back_to_old_refresh(config, expired_tokens):
    """If Adobe doesn't return a new refresh_token, keep using the old one."""
    respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-2",
                # no refresh_token in response
                "expires_in": 86400,
            },
        )
    )
    new_tokens = await refresh_tokens(config, expired_tokens)
    assert new_tokens.refresh_token == expired_tokens.refresh_token


@pytest.mark.asyncio
async def test_refresh_tokens_raises_if_no_refresh_token(config):
    tokens = Tokens(access_token="a", refresh_token="", expires_at=0)
    with pytest.raises(AuthError, match="No refresh token"):
        await refresh_tokens(config, tokens)
