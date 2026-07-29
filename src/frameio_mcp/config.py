"""Configuration loading — env vars, file paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from CWD if present; also try ~/.frameio-mcp/.env
load_dotenv()
load_dotenv(dotenv_path=Path.home() / ".frameio-mcp" / ".env", override=False)


# Adobe IMS endpoints
ADOBE_IMS_AUTHORIZE_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
ADOBE_IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
ADOBE_IMS_DISCOVERY_URL = (
    "https://ims-na1.adobelogin.com/.well-known/openid-configuration"
)

# Frame.io v4 API
FRAMEIO_API_BASE_URL = "https://api.frame.io/v4"

# Scopes Frame.io v4 authorization depends on.
#
# `additional_info.roles` is the one that bites: without it IMS still issues a
# perfectly valid token, v2 endpoints keep working, and every v4 endpoint returns a
# blanket 401 with no hint that a scope is missing. `email` and `profile` are needed
# alongside it for v4 to resolve the user. `offline_access` is what earns a refresh
# token, without which users re-authenticate daily.
REQUIRED_V4_SCOPES = frozenset(
    {"openid", "email", "profile", "offline_access", "additional_info.roles"}
)

# Adobe IMS expects a comma-separated list with no spaces.
DEFAULT_SCOPES = "openid,email,profile,offline_access,additional_info.roles"


def resolve_tokens_path() -> Path:
    """Where tokens live, resolved without requiring Adobe credentials.

    Commands that only touch the local token file (logout, status) should not fail
    because a client secret is absent. Deleting a token you already have does not
    require credentials to obtain a new one.
    """
    return Path(
        os.getenv(
            "FRAMEIO_TOKENS_PATH",
            str(Path.home() / ".frameio-mcp" / "tokens.json"),
        )
    )


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    oauth_relay_url: str
    tokens_path: Path
    scopes: str = DEFAULT_SCOPES

    @classmethod
    def from_env(cls) -> Config:
        """Load config from environment variables. Raises if required vars are missing."""
        client_id = os.getenv("FRAMEIO_CLIENT_ID")
        client_secret = os.getenv("FRAMEIO_CLIENT_SECRET")
        oauth_relay_url = os.getenv(
            "FRAMEIO_OAUTH_RELAY_URL",
            "https://aiscalestudio.github.io/frameio-mcp/callback.html",
        )
        tokens_path_str = str(resolve_tokens_path())
        scopes = os.getenv("FRAMEIO_SCOPES", DEFAULT_SCOPES)

        missing = []
        if not client_id:
            missing.append("FRAMEIO_CLIENT_ID")
        if not client_secret:
            missing.append("FRAMEIO_CLIENT_SECRET")
        if missing:
            raise OSError(
                f"Missing required env vars: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill in your Adobe Developer Console credentials."
            )

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            oauth_relay_url=oauth_relay_url,
            tokens_path=Path(tokens_path_str),
            scopes=scopes,
        )


def ensure_tokens_dir(tokens_path: Path) -> None:
    """Create parent directory for tokens file with restrictive permissions."""
    tokens_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
