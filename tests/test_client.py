"""Tests for the FrameIOClient — auth header injection, 401 refresh, 429 backoff."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from frameio_mcp.client import FrameIOClient, FrameIOError, RateLimitError
from frameio_mcp.config import ADOBE_IMS_TOKEN_URL, FRAMEIO_API_BASE_URL


@pytest.mark.asyncio
@respx.mock
async def test_request_injects_bearer_token(config, valid_tokens):
    route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "user-1"}})
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        result = await client.get_me()
    assert result == {"id": "user-1"}
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {valid_tokens.access_token}"


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_refresh_and_retry(config, valid_tokens):
    # First call returns 401, refresh returns new token, retry returns 200
    api_route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        side_effect=[
            httpx.Response(401, json={"errors": [{"title": "Unauthorized"}]}),
            httpx.Response(200, json={"data": {"id": "user-1"}}),
        ]
    )
    refresh_route = respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-fresh",
                "refresh_token": "rt-fresh",
                "expires_in": 86400,
            },
        )
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        result = await client.get_me()
    assert result == {"id": "user-1"}
    assert api_route.call_count == 2
    assert refresh_route.call_count == 1
    # Second API call used the refreshed token
    assert api_route.calls[-1].request.headers["authorization"] == "Bearer at-fresh"


@pytest.mark.asyncio
@respx.mock
async def test_429_triggers_backoff_and_retry(config, valid_tokens, mocker):
    # Mock sleep so the test isn't slow
    sleep_spy = mocker.patch("asyncio.sleep", return_value=None)

    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        side_effect=[
            httpx.Response(429, json={"errors": [{"title": "Too Many Requests"}]}),
            httpx.Response(200, json={"data": {"id": "user-1"}}),
        ]
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        result = await client.get_me()
    assert result == {"id": "user-1"}
    assert sleep_spy.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_429_exhausted_raises(config, valid_tokens, mocker):
    mocker.patch("asyncio.sleep", return_value=None)
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(429, json={"errors": [{"title": "429"}]})
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        with pytest.raises(RateLimitError, match="retries exhausted"):
            await client.get_me()


@pytest.mark.asyncio
@respx.mock
async def test_500_raises_frameio_error(config, valid_tokens):
    respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(500, text="server error")
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        with pytest.raises(FrameIOError, match="500"):
            await client.get_me()


@pytest.mark.asyncio
@respx.mock
async def test_create_comment_sends_microseconds(config, valid_tokens):
    route = respx.post(
        f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
    ).mock(
        return_value=httpx.Response(
            201,
            json={"data": {"id": "comment-1", "text": "hi", "timestamp": 1500000}},
        )
    )
    async with FrameIOClient(config, tokens=valid_tokens) as client:
        result = await client.create_comment(
            account_id="acct-1",
            file_id="file-1",
            text="hi",
            timestamp_microseconds=1_500_000,
        )
    assert result["id"] == "comment-1"
    import json as jsonlib
    body = jsonlib.loads(route.calls.last.request.content)
    assert body == {"data": {"text": "hi", "timestamp": 1500000}}


@pytest.mark.asyncio
@respx.mock
async def test_expired_token_refreshed_before_request(config, expired_tokens):
    """Auto-refresh when tokens are already known to be expired."""
    refresh_route = respx.post(ADOBE_IMS_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_in": 86400,
            },
        )
    )
    api_route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "u"}})
    )
    async with FrameIOClient(config, tokens=expired_tokens) as client:
        await client.get_me()
    # Refresh happened before the API call
    assert refresh_route.call_count == 1
    assert api_route.calls[0].request.headers["authorization"] == "Bearer at-new"
