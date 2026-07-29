"""Probe a running spike server. Answers Q1-Q3 without needing an Adobe login.

Usage:
    .venv/bin/python spike/probe.py                 # run all checks
    .venv/bin/python spike/probe.py --client-id ID  # re-check a client from a prior run

The persistence check is two-phase on purpose:
  1. run the probe, it registers a client and prints the client_id
  2. restart the server process
  3. re-run with --client-id; if the server still knows it, state survived
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

BASE = "http://localhost:8000"


def show(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        for line in detail.splitlines():
            print(f"      {line}")
    return ok


def check_protected_resource_metadata() -> bool:
    """Q2a: Claude reads this first to discover where to authenticate."""
    # FastMCP 3.x scopes this per resource path, so it is /...-resource/mcp not /...-resource
    r = httpx.get(f"{BASE}/.well-known/oauth-protected-resource/mcp", timeout=10)
    if r.status_code != 200:
        return show("protected resource metadata", False, f"HTTP {r.status_code}")
    body = r.json()
    servers = body.get("authorization_servers", [])
    return show(
        "protected resource metadata",
        bool(servers),
        f"resource: {body.get('resource')}\nauth servers: {servers}",
    )


def check_authorization_server_metadata() -> bool:
    """Q2b: must advertise a registration endpoint, else Claude cannot self-register."""
    r = httpx.get(f"{BASE}/.well-known/oauth-authorization-server", timeout=10)
    if r.status_code != 200:
        return show("authorization server metadata", False, f"HTTP {r.status_code}")
    body = r.json()
    registration = body.get("registration_endpoint")
    return show(
        "authorization server metadata",
        bool(registration),
        f"authorize:    {body.get('authorization_endpoint')}\n"
        f"token:        {body.get('token_endpoint')}\n"
        f"registration: {registration}",
    )


def check_unauthenticated_mcp_is_rejected() -> bool:
    """A public MCP endpoint that answers without a token is a security failure."""
    r = httpx.post(
        f"{BASE}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
        timeout=10,
    )
    challenge = r.headers.get("www-authenticate", "")
    return show(
        "unauthenticated /mcp rejected",
        r.status_code == 401,
        f"HTTP {r.status_code}\nWWW-Authenticate: {challenge or '(absent)'}",
    )


def register_client() -> str | None:
    """Q3 phase 1: register a client the way Claude would."""
    r = httpx.post(
        f"{BASE}/register",
        json={
            "client_name": "spike-persistence-probe",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        timeout=15,
    )
    if r.status_code not in (200, 201):
        show("dynamic client registration", False, f"HTTP {r.status_code}\n{r.text[:300]}")
        return None
    client_id = r.json().get("client_id")
    show("dynamic client registration", bool(client_id), f"client_id: {client_id}")
    return client_id


def check_client_still_known(client_id: str) -> bool:
    """Q3 phase 2: after a restart, does the server still recognise the registration?

    A 401 or 404 here is the ephemeral-state bug. In production it appears as users
    being silently logged out whenever a cold start or scale-out replaces the process.
    """
    r = httpx.post(
        f"{BASE}/token",
        data={
            "grant_type": "authorization_code",
            "code": "deliberately-invalid",
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        },
        timeout=15,
    )
    body = r.text[:300]
    # An unknown client is rejected as invalid_client. A known client with a bad code
    # is rejected as invalid_grant. That difference is the whole signal.
    unknown_client = "invalid_client" in body or r.status_code == 401
    return show(
        "client survives restart",
        not unknown_client,
        f"HTTP {r.status_code}\n{body}\n"
        + (
            "server does not recognise the client: state was LOST"
            if unknown_client
            else "server recognises the client (rejected the fake code, as expected)"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", help="Re-check a client registered before a restart")
    args = parser.parse_args()

    try:
        httpx.get(f"{BASE}/.well-known/oauth-authorization-server", timeout=5)
    except httpx.ConnectError:
        print(f"No server at {BASE}. Start it with: .venv/bin/python spike/server.py")
        return 2

    if args.client_id:
        print("Phase 2: checking whether the registration survived the restart\n")
        return 0 if check_client_still_known(args.client_id) else 1

    print("Phase 1: metadata, auth enforcement, and registration\n")
    results = [
        check_protected_resource_metadata(),
        check_authorization_server_metadata(),
        check_unauthenticated_mcp_is_rejected(),
    ]
    client_id = register_client()
    results.append(client_id is not None)

    print()
    if client_id:
        print("Next: restart the server process, then run")
        print(f"  .venv/bin/python spike/probe.py --client-id {client_id}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
