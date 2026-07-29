"""Configuration for the hosted MCP server.

Kept separate from `config.Config`, which serves the local diagnostic CLI. The two
have genuinely different requirements: the CLI stores a token on disk for one user,
while the hosted server stores nothing per user and must behave identically across
many short-lived instances.

Every value here is required. None of them have a safe default, because the unsafe
defaults all fail the same way: they work on one machine and break intermittently
once a second instance exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import DEFAULT_SCOPES

# Path OIDCProxy serves its OAuth callback on. Must match the redirect URI
# registered in the Adobe Developer Console exactly.
OAUTH_CALLBACK_PATH = "/auth/callback"

_REQUIRED_ENV_VARS = (
    "FRAMEIO_CLIENT_ID",
    "FRAMEIO_CLIENT_SECRET",
    "FRAMEIO_BASE_URL",
    "JWT_SIGNING_KEY",
    "STORAGE_ENCRYPTION_KEY",
    "REDIS_URL",
)


@dataclass(frozen=True)
class ServerConfig:
    client_id: str
    client_secret: str
    base_url: str
    jwt_signing_key: str
    storage_encryption_key: str
    redis_url: str

    @property
    def redirect_uri(self) -> str:
        """Where Adobe sends the user back. Registered in the Adobe console."""
        return f"{self.base_url}{OAUTH_CALLBACK_PATH}"

    @property
    def required_scopes(self) -> list[str]:
        """Scopes proven to work against live Frame.io v4. See ai_docs/PHASE0_RUNBOOK.md."""
        return DEFAULT_SCOPES.split(",")

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Load and validate every setting. Reports all problems at once."""
        values = {name: os.getenv(name) for name in _REQUIRED_ENV_VARS}

        missing = [name for name, value in values.items() if not value]
        if missing:
            raise OSError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"See ai_docs/PHASE0_RUNBOOK.md for what each one is for."
            )

        base_url = _validate_base_url(values["FRAMEIO_BASE_URL"])  # type: ignore[arg-type]

        return cls(
            client_id=values["FRAMEIO_CLIENT_ID"],  # type: ignore[arg-type]
            client_secret=values["FRAMEIO_CLIENT_SECRET"],  # type: ignore[arg-type]
            base_url=base_url,
            jwt_signing_key=values["JWT_SIGNING_KEY"],  # type: ignore[arg-type]
            storage_encryption_key=values["STORAGE_ENCRYPTION_KEY"],  # type: ignore[arg-type]
            redis_url=values["REDIS_URL"],  # type: ignore[arg-type]
        )


def _validate_base_url(raw: str) -> str:
    """Normalise the public origin and refuse anything that would leak credentials."""
    base_url = raw.rstrip("/")
    parsed = urlparse(base_url)

    is_local = parsed.hostname in ("localhost", "127.0.0.1")
    if parsed.scheme != "https" and not is_local:
        raise OSError(
            f"FRAMEIO_BASE_URL must use https, got {base_url!r}. OAuth authorization "
            f"codes and bearer tokens travel over this origin. Plain http is only "
            f"allowed for localhost."
        )

    return base_url
