"""Phase 0 Step 2 spike: does a FastMCP OIDCProxy server survive a serverless lifecycle?

This is throwaway code. It exists to answer three questions before the real refactor
commits to an architecture:

  Q1. Does OIDCProxy wire up against Adobe IMS discovery at all?
  Q2. Does the server publish the OAuth metadata Claude needs to auto-register,
      so a Cowork user never types a client ID or secret?
  Q3. Do OAuth client registrations survive the process being replaced?

Q3 is the one that matters. Vercel Functions are ephemeral and horizontally scaled, so
any OAuth state held in memory or on local disk is lost between invocations. If that
happens, users get silently disconnected on every cold start, which presents as
"it worked yesterday" rather than as an error.

Run:
    STORAGE=memory .venv/bin/python spike/server.py     # expected to FAIL Q3
    STORAGE=disk   .venv/bin/python spike/server.py     # survives restart, single host only
    STORAGE=redis  REDIS_URL=... .venv/bin/python spike/server.py
"""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCProxy

ADOBE_DISCOVERY = "https://ims-na1.adobelogin.com/.well-known/openid-configuration"
REQUIRED_SCOPES = [
    "openid",
    "email",
    "profile",
    "offline_access",
    "additional_info.roles",
]


def build_client_storage():
    """Pick a storage backend. The choice is the entire point of this spike."""
    mode = os.getenv("STORAGE", "memory")

    if mode == "memory":
        from key_value.aio.stores.memory import MemoryStore

        return MemoryStore(), "memory (lost on restart)"

    if mode == "disk":
        from key_value.aio.stores.disk import DiskStore

        return DiskStore(directory=".spike-storage"), "disk (lost on Vercel)"

    if mode == "redis":
        from cryptography.fernet import Fernet
        from key_value.aio.stores.redis import RedisStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        url = os.environ["REDIS_URL"]
        key = os.environ["STORAGE_ENCRYPTION_KEY"]
        store = FernetEncryptionWrapper(
            key_value=RedisStore(url=url), fernet=Fernet(key)
        )
        return store, "redis + fernet (survives everywhere)"

    raise SystemExit(f"Unknown STORAGE={mode!r}. Use memory | disk | redis.")


def build_server() -> FastMCP:
    storage, storage_label = build_client_storage()
    base_url = os.getenv("BASE_URL", "http://localhost:8000")

    auth = OIDCProxy(
        config_url=ADOBE_DISCOVERY,
        client_id=os.environ["FRAMEIO_CLIENT_ID"],
        client_secret=os.environ["FRAMEIO_CLIENT_SECRET"],
        base_url=base_url,
        required_scopes=REQUIRED_SCOPES,
        # A fixed signing key is mandatory across instances. If this is derived per
        # process, a token minted by one Vercel instance is rejected by the next.
        jwt_signing_key=os.environ["JWT_SIGNING_KEY"],
        client_storage=storage,
    )

    # Note: in FastMCP 3.x `stateless_http` is no longer a FastMCP() kwarg. It moved to
    # run_http_async()/http_app(), or the FASTMCP_STATELESS_HTTP env var. It is required
    # on serverless: there is no process to hold a session between requests.
    mcp = FastMCP("frameio-spike", auth=auth)

    @mcp.tool
    async def whoami() -> dict:
        """Report what the server knows about the caller, without calling Frame.io.

        The spike needs to know whether the token handed to a tool is the upstream
        Adobe token (usable directly against Frame.io) or a FastMCP-issued token that
        has to be exchanged. That determines how the real tools get threaded.
        """
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is None:
            return {"authenticated": False}

        raw = token.token or ""
        return {
            "authenticated": True,
            "subject": token.subject,
            "client_id": token.client_id,
            "scopes": token.scopes,
            "token_segments": len(raw.split(".")),
            "token_prefix": raw[:12],
            "claim_keys": sorted((token.claims or {}).keys()),
        }

    print(f"storage backend: {storage_label}")
    print(f"base url:        {base_url}")
    return mcp


if __name__ == "__main__":
    build_server().run(
        transport="http", host="127.0.0.1", port=8000, stateless_http=True
    )
