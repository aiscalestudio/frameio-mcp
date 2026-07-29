"""Builds the authenticated ASGI application for the hosted deployment.

Kept apart from `server.py` so the tool definitions stay importable without any
hosting configuration. That separation is what lets the local stdio CLI and the tests
use the tools without a Redis URL or a signing key.

Three settings here are load-bearing on serverless and each fails silently rather than
loudly if it is wrong:

  - `client_storage` must be shared, not per-process. Vercel replaces instances freely;
    an in-memory or on-disk store means OAuth registrations vanish on every cold start
    and users appear to be randomly signed out. Verified in spike/FINDINGS.md.
  - `jwt_signing_key` must be a fixed value. If it is derived per process, a token
    minted by one instance is rejected by the next.
  - `stateless_http` must be on. There is no long-lived process to hold a session.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.auth.oidc_proxy import OIDCProxy

from .adobe_verifier import AdobeIMSTokenVerifier
from .config import ADOBE_IMS_DISCOVERY_URL
from .server import mcp
from .server_config import OAUTH_CALLBACK_PATH, ServerConfig


def build_client_storage(config: ServerConfig) -> Any:
    """Redis-backed, encrypted storage for OAuth client and token records.

    Encryption matters because upstream Adobe tokens are stored here. Without the
    wrapper they sit in Redis in plaintext.
    """
    from cryptography.fernet import Fernet
    from key_value.aio.stores.redis import RedisStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    return FernetEncryptionWrapper(
        key_value=RedisStore(url=config.redis_url),
        fernet=Fernet(config.storage_encryption_key),
    )


def build_auth(config: ServerConfig, client_storage: Any | None = None) -> OIDCProxy:
    """Front the server with Adobe IMS.

    Adobe publishes a standard discovery document, so the authorize, token, and JWKS
    endpoints come from `config_url` rather than being hardcoded. Adobe does not
    support Dynamic Client Registration, which is exactly the gap OIDCProxy fills:
    Claude registers with us, and we hold the single Adobe credential.

    `client_storage` is injectable so tests can exercise the wiring without a Redis.
    Production must not pass it: the default is the shared store this depends on.
    """
    return OIDCProxy(
        config_url=ADOBE_IMS_DISCOVERY_URL,
        client_id=config.client_id,
        client_secret=config.client_secret,
        base_url=config.base_url,
        redirect_path=OAUTH_CALLBACK_PATH,
        jwt_signing_key=config.jwt_signing_key,
        client_storage=client_storage or build_client_storage(config),
        # Adobe IMS tokens cannot be verified as JWTs. Every token header carries
        # `x5u: "ims_na1-key-at-1.cer"`, a bare filename where RFC 7515 requires a URI,
        # so a conforming library rejects the token on header validation before checking
        # the signature. Both the access token and the id_token carry it. The symptom is
        # a server that authenticates a user and then rejects the credentials it just
        # issued. See adobe_verifier.py for the full account.
        token_verifier=AdobeIMSTokenVerifier(
            client_id=config.client_id,
            required_scopes=config.required_scopes,
            base_url=config.base_url,
        ),
    )


def create_app(config: ServerConfig | None = None, client_storage: Any | None = None):
    """Return the ASGI app. Raises at import time if the environment is incomplete."""
    config = config or ServerConfig.from_env()
    mcp.auth = build_auth(config, client_storage=client_storage)
    return mcp.http_app(stateless_http=True)
