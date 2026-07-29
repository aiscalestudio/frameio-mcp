"""Staged Frame.io v4 entitlement checks.

Frame.io v4 fails in a way that is genuinely hard to read: Adobe IMS issues a valid
token, v2 endpoints keep working, and every v4 endpoint returns a bare 401. The cause
is almost always one of three things, and they need to be told apart:

  1. the token was never granted `additional_info.roles` (a scope problem)
  2. the Adobe ID is not linked to a Frame.io account (an account-linking problem)
  3. the user is not assigned a Frame.io v4 product profile (an entitlement problem)

This module runs the checks in dependency order and reports the first failure with the
specific remedy, rather than surfacing "401" and leaving the caller to guess.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any

from .auth import Tokens
from .client import FrameIOClient, FrameIOError
from .config import REQUIRED_V4_SCOPES, Config


@dataclass
class CheckResult:
    """Outcome of a single diagnostic stage."""

    name: str
    passed: bool
    detail: str
    remedy: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def decode_token_scopes(access_token: str) -> set[str]:
    """Read the `scope` claim out of an IMS access token without verifying it.

    The token is only being inspected to report what Adobe actually granted, which is
    routinely narrower than what was requested. Signature verification is deliberately
    skipped: this is a local diagnostic, not an authorization decision.
    """
    parts = access_token.split(".")
    if len(parts) != 3:
        return set()

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore stripped base64url padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return set()

    raw_scope = claims.get("scope") or claims.get("scp") or ""
    if isinstance(raw_scope, list):
        return {str(s).strip() for s in raw_scope if str(s).strip()}
    return {s.strip() for s in str(raw_scope).replace(" ", ",").split(",") if s.strip()}


def check_granted_scopes(tokens: Tokens) -> CheckResult:
    """Compare the scopes Adobe actually granted against what v4 requires."""
    granted = decode_token_scopes(tokens.access_token)

    if not granted:
        return CheckResult(
            name="Granted scopes",
            passed=True,
            detail="Could not read scopes from the token (opaque or unexpected format). "
            "Skipping this check; the live v4 calls below are the real test.",
            data={"granted": []},
        )

    missing = REQUIRED_V4_SCOPES - granted
    if missing:
        return CheckResult(
            name="Granted scopes",
            passed=False,
            detail=f"Token is missing: {', '.join(sorted(missing))}",
            remedy=(
                "In the Adobe Developer Console, open your project's OAuth Web App "
                "credential and add the missing scopes, then run `frameio-mcp login` "
                "again. An existing token keeps the scopes it was issued with, so "
                "re-authenticating is required."
            ),
            data={"granted": sorted(granted), "missing": sorted(missing)},
        )

    return CheckResult(
        name="Granted scopes",
        passed=True,
        detail=f"All {len(REQUIRED_V4_SCOPES)} required scopes granted",
        data={"granted": sorted(granted)},
    )


async def check_v4_identity(client: FrameIOClient) -> CheckResult:
    """GET /v4/me. The narrowest possible v4 read, so it isolates entitlement."""
    try:
        me = await client.get_me()
    except FrameIOError as e:
        return CheckResult(
            name="v4 identity (GET /me)",
            passed=False,
            detail=str(e),
            remedy=(
                "A 401 here means the token is not authorized for Frame.io v4 at all. "
                "Check, in order: (1) `additional_info.roles` is in the granted scopes "
                "above, (2) this Adobe ID is linked to your Frame.io account, "
                "(3) the user is assigned a Frame.io v4 product profile in the Adobe "
                "Admin Console. Item 3 is admin-only and the most commonly missed."
            ),
        )

    return CheckResult(
        name="v4 identity (GET /me)",
        passed=True,
        detail=f"Authenticated as {me.get('email') or me.get('name') or me.get('id')}",
        data={"user": me},
    )


async def check_v4_accounts(client: FrameIOClient) -> CheckResult:
    """List accounts. Confirms the token resolves to real Frame.io tenancy."""
    try:
        accounts = await client.list_accounts()
    except FrameIOError as e:
        return CheckResult(
            name="v4 accounts",
            passed=False,
            detail=str(e),
            remedy="The token authenticates but cannot enumerate accounts. "
            "Confirm the Frame.io API is added to the Adobe Developer Console project.",
        )

    if not accounts:
        return CheckResult(
            name="v4 accounts",
            passed=False,
            detail="Zero accounts returned",
            remedy=(
                "The call succeeded but this Adobe ID sees no Frame.io accounts. It is "
                "authenticated but not a member of any Frame.io workspace."
            ),
        )

    names = [a.get("name") or a.get("id") for a in accounts]
    return CheckResult(
        name="v4 accounts",
        passed=True,
        detail=f"{len(accounts)} account(s): {', '.join(str(n) for n in names)}",
        data={"accounts": accounts},
    )


async def check_v4_read_comments(
    client: FrameIOClient, account_id: str, file_id: str
) -> CheckResult:
    """Read comments on a real file. Proves per-file read authorization."""
    try:
        result = await client.list_comments(account_id, file_id, page_size=5)
    except FrameIOError as e:
        return CheckResult(
            name="v4 read (list comments)",
            passed=False,
            detail=str(e),
            remedy="Identity works but this file is not readable. Confirm the URL "
            "points at a file this user can open in the Frame.io UI.",
        )

    comments = result.get("data", [])
    return CheckResult(
        name="v4 read (list comments)",
        passed=True,
        detail=f"Read {len(comments)} comment(s) from the file",
        data={"count": len(comments)},
    )


async def check_v4_write_comment(
    client: FrameIOClient, account_id: str, file_id: str, text: str
) -> CheckResult:
    """Post a real comment. This is the gate: writes need broader authorization than reads."""
    try:
        created = await client.create_comment(
            account_id=account_id,
            file_id=file_id,
            text=text,
            timestamp_microseconds=0,
        )
    except FrameIOError as e:
        return CheckResult(
            name="v4 write (post comment)",
            passed=False,
            detail=str(e),
            remedy=(
                "Reads work but writes do not. This is usually a Frame.io role problem "
                "rather than an OAuth problem: the user needs comment permission on the "
                "project, not just view access."
            ),
        )

    comment_id = created.get("id")
    return CheckResult(
        name="v4 write (post comment)",
        passed=True,
        detail=f"Created comment {comment_id}. Delete it in the Frame.io UI when done.",
        data={"comment_id": comment_id, "comment": created},
    )


async def run_entitlement_checks(
    config: Config,
    tokens: Tokens,
    account_id: str | None = None,
    file_id: str | None = None,
    write_probe_text: str | None = None,
) -> list[CheckResult]:
    """Run the checks in dependency order, stopping at the first failure.

    Stopping early is deliberate: once identity fails, every later failure is a
    downstream echo of the same cause and only adds noise.
    """
    results: list[CheckResult] = [check_granted_scopes(tokens)]

    # auto_refresh_on_401 is off on purpose: a refresh attempt would rewrite an
    # entitlement failure as an authentication failure and hide the real cause.
    async with FrameIOClient(
        config, tokens=tokens, auto_refresh_on_401=False
    ) as client:
        identity = await check_v4_identity(client)
        results.append(identity)
        if not identity.passed:
            return results

        accounts = await check_v4_accounts(client)
        results.append(accounts)
        if not accounts.passed:
            return results

        if not file_id:
            return results

        resolved_account = account_id or _first_account_id(accounts)
        if not resolved_account:
            return results

        read = await check_v4_read_comments(client, resolved_account, file_id)
        results.append(read)
        if not read.passed or not write_probe_text:
            return results

        results.append(
            await check_v4_write_comment(
                client, resolved_account, file_id, write_probe_text
            )
        )

    return results


def _first_account_id(accounts_result: CheckResult) -> str | None:
    for account in accounts_result.data.get("accounts", []):
        if account.get("id"):
            return str(account["id"])
    return None
