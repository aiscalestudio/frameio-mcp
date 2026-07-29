"""Guard tests for the Adobe IMS OIDC discovery document.

The hosted server delegates its whole OAuth configuration to this document, so if Adobe
moves an endpoint the failure would otherwise surface as an unexplained login loop in
Claude Cowork. These tests turn that into a named test failure.

Marked `network` because they call Adobe. Skip with: pytest -m "not network"
"""

from __future__ import annotations

import httpx
import pytest

from frameio_mcp.config import (
    ADOBE_IMS_AUTHORIZE_URL,
    ADOBE_IMS_DISCOVERY_URL,
    ADOBE_IMS_TOKEN_URL,
)

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def discovery() -> dict:
    response = httpx.get(ADOBE_IMS_DISCOVERY_URL, timeout=20.0)
    response.raise_for_status()
    return response.json()


def test_discovery_document_is_reachable(discovery):
    assert discovery["issuer"] == "https://ims-na1.adobelogin.com"


def test_hardcoded_endpoints_still_match_discovery(discovery):
    """The constants and the discovery document must not drift apart."""
    assert discovery["authorization_endpoint"] == ADOBE_IMS_AUTHORIZE_URL
    assert discovery["token_endpoint"] == ADOBE_IMS_TOKEN_URL


def test_discovery_exposes_a_jwks_uri(discovery):
    """OIDCProxy needs this to verify tokens without calling Adobe per request."""
    assert discovery["jwks_uri"] == "https://ims-na1.adobelogin.com/ims/keys"


def test_authorization_code_flow_is_supported(discovery):
    assert "code" in discovery["response_types_supported"]


def test_jwks_serves_a_usable_signing_key(discovery):
    response = httpx.get(discovery["jwks_uri"], timeout=20.0)
    response.raise_for_status()
    keys = response.json()["keys"]

    signing_keys = [k for k in keys if k.get("use") == "sig"]
    assert signing_keys, "No signing keys published"
    assert any(k.get("alg") == "RS256" for k in signing_keys)
