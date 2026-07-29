"""Tests for FrameIOClient: auth header injection, 429 backoff, error surfacing.

The client takes an access token per request and does not refresh. Refresh belongs to
whoever owns the token lifecycle, and the two callers own it differently:

  - hosted server: FastMCP's OAuthProxy refreshes the upstream Adobe token for us,
    under a lock that prevents concurrent requests racing
  - local CLI: `get_valid_tokens()` refreshes before handing the token over

Putting refresh inside the client would mean duplicating that logic and, worse,
writing to a token file that does not exist on the hosted side.
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest
import respx

from frameio_mcp.client import FrameIOClient, FrameIOError, RateLimitError
from frameio_mcp.config import FRAMEIO_API_BASE_URL


@respx.mock
async def test_request_injects_the_bearer_token():
    route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "user-1"}})
    )

    async with FrameIOClient("token-abc") as client:
        result = await client.get_me()

    assert result == {"id": "user-1"}
    assert route.calls.last.request.headers["authorization"] == "Bearer token-abc"


async def test_empty_token_is_rejected_before_any_request():
    """Failing at construction beats a confusing 401 from Frame.io."""
    with pytest.raises(ValueError, match="access token"):
        FrameIOClient("")


@respx.mock
async def test_401_is_surfaced_not_silently_retried():
    """A 401 means the caller's token is bad; only the caller can fix that.

    The previous behaviour refreshed and retried here, which rewrote entitlement
    failures as authentication failures and made them very hard to diagnose.
    """
    route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(401, json={"errors": ["Unauthorized"]})
    )

    async with FrameIOClient("stale-token") as client:
        with pytest.raises(FrameIOError, match="401"):
            await client.get_me()

    assert route.call_count == 1, "must not retry"


@respx.mock
async def test_429_backs_off_and_retries(mocker):
    sleep_spy = mocker.patch("asyncio.sleep", return_value=None)
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        side_effect=[
            httpx.Response(429, json={"errors": ["Too Many Requests"]}),
            httpx.Response(200, json={"data": {"id": "user-1"}}),
        ]
    )

    async with FrameIOClient("token-abc") as client:
        result = await client.get_me()

    assert result == {"id": "user-1"}
    assert sleep_spy.call_count == 1


@respx.mock
async def test_429_gives_up_after_max_retries(mocker):
    mocker.patch("asyncio.sleep", return_value=None)
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(429, json={"errors": ["429"]})
    )

    async with FrameIOClient("token-abc") as client:
        with pytest.raises(RateLimitError, match="retries exhausted"):
            await client.get_me()


@respx.mock
async def test_500_raises_frameio_error():
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(500, text="server error")
    )

    async with FrameIOClient("token-abc") as client:
        with pytest.raises(FrameIOError, match="500"):
            await client.get_me()


@respx.mock
async def test_create_comment_sends_microseconds():
    route = respx.post(
        f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
    ).mock(
        return_value=httpx.Response(
            201, json={"data": {"id": "comment-1", "text": "hi", "timestamp": 1500000}}
        )
    )

    async with FrameIOClient("token-abc") as client:
        result = await client.create_comment(
            account_id="acct-1",
            file_id="file-1",
            text="hi",
            timestamp_microseconds=1_500_000,
        )

    assert result["id"] == "comment-1"
    body = jsonlib.loads(route.calls.last.request.content)
    assert body == {"data": {"text": "hi", "timestamp": 1500000}}


@respx.mock
async def test_204_returns_empty_dict():
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(return_value=httpx.Response(204))

    async with FrameIOClient("token-abc") as client:
        assert await client.get_me() == {}


async def test_use_outside_context_manager_fails_loudly():
    client = FrameIOClient("token-abc")
    with pytest.raises(RuntimeError, match="async with"):
        await client.get_me()
