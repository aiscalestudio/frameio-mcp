"""OAuth 2.0 flow with Adobe IMS for Frame.io API access."""

from __future__ import annotations

import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import (
    ADOBE_IMS_AUTHORIZE_URL,
    ADOBE_IMS_TOKEN_URL,
    Config,
    ensure_tokens_dir,
)


class AuthError(Exception):
    """Raised when authentication or token refresh fails."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp
    account_id: str | None = None
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        # Refresh a bit before actual expiry to avoid mid-request failures
        return time.time() >= (self.expires_at - 60)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Tokens:
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            account_id=data.get("account_id"),
            token_type=data.get("token_type", "Bearer"),
        )


def generate_state() -> str:
    """Generate a random CSRF token for the OAuth `state` parameter."""
    return secrets.token_urlsafe(32)


def build_authorize_url(config: Config, state: str) -> str:
    """Build the Adobe IMS OAuth authorization URL."""
    params = {
        "client_id": config.client_id,
        "scope": config.scopes,
        "response_type": "code",
        "redirect_uri": config.oauth_relay_url,
        "state": state,
    }
    return f"{ADOBE_IMS_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_tokens(config: Config, code: str) -> Tokens:
    """POST to Adobe IMS token endpoint to exchange auth code for tokens."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            ADOBE_IMS_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.oauth_relay_url,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise AuthError(
            f"Token exchange failed ({response.status_code}): {response.text[:500]}"
        )

    payload = response.json()
    if "access_token" not in payload:
        raise AuthError(f"Token response missing access_token: {payload}")

    expires_in = int(payload.get("expires_in", 86400))
    return Tokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_at=time.time() + expires_in,
        token_type=payload.get("token_type", "Bearer"),
    )


async def refresh_tokens(config: Config, tokens: Tokens) -> Tokens:
    """POST to Adobe IMS token endpoint to refresh an expired access token."""
    if not tokens.refresh_token:
        raise AuthError(
            "No refresh token available. Run `frameio-mcp login` to re-authenticate."
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            ADOBE_IMS_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": tokens.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise AuthError(
            f"Token refresh failed ({response.status_code}): {response.text[:500]}. "
            f"Run `frameio-mcp login` to re-authenticate."
        )

    payload = response.json()
    expires_in = int(payload.get("expires_in", 86400))
    return Tokens(
        access_token=payload["access_token"],
        # Adobe returns a new refresh_token on refresh; fall back to old one if not
        refresh_token=payload.get("refresh_token", tokens.refresh_token),
        expires_at=time.time() + expires_in,
        account_id=tokens.account_id,
        token_type=payload.get("token_type", "Bearer"),
    )


def save_tokens(tokens: Tokens, path: Path) -> None:
    """Write tokens to disk atomically with 0600 permissions."""
    ensure_tokens_dir(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(tokens.to_dict(), indent=2))
    tmp_path.chmod(0o600)
    tmp_path.replace(path)


def load_tokens(path: Path) -> Tokens | None:
    """Read tokens from disk. Returns None if no tokens file exists."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return Tokens.from_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        raise AuthError(f"Corrupted tokens file at {path}: {e}") from e


async def get_valid_tokens(config: Config) -> Tokens:
    """Load tokens from disk. Refresh if expired. Raise if not authenticated."""
    tokens = load_tokens(config.tokens_path)
    if tokens is None:
        raise AuthError(
            "Not authenticated. Run `frameio-mcp login` to sign in with your Adobe ID."
        )
    if tokens.is_expired:
        tokens = await refresh_tokens(config, tokens)
        save_tokens(tokens, config.tokens_path)
    return tokens


def clear_tokens(path: Path) -> bool:
    """Delete the tokens file. Returns True if a file was removed."""
    if path.exists():
        path.unlink()
        return True
    return False
