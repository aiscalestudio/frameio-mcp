"""Frame.io v4 REST API client.

Takes an access token per instance and does not manage its lifecycle. Refresh belongs
to whoever owns the token:

  - the hosted server gets an already-fresh Adobe token from FastMCP's OAuthProxy,
    which refreshes upstream under a lock
  - the local CLI calls `get_valid_tokens()`, which refreshes before handing it over

Keeping refresh out of here is what lets the same client serve both. It also stops a
401 from being silently rewritten as an authentication problem when the real cause is
a missing Frame.io entitlement.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any

import httpx

from .config import FRAMEIO_API_BASE_URL


class FrameIOError(Exception):
    """Raised on non-recoverable Frame.io API errors."""


class RateLimitError(FrameIOError):
    """Raised when rate limit exhausted after retries."""


class FrameIOClient:
    """Async client for the Frame.io v4 REST API. Handles 429 backoff.

    Usage:
        async with FrameIOClient(access_token) as client:
            me = await client.get_me()
    """

    MAX_RETRIES = 4

    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError(
                "FrameIOClient requires a non-empty access token. The hosted server "
                "reads it from get_access_token(); the CLI from get_valid_tokens()."
            )
        self._access_token = access_token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> FrameIOClient:
        self._client = httpx.AsyncClient(
            base_url=FRAMEIO_API_BASE_URL,
            timeout=60.0,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._access_token}",
            },
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        retry_count: int = 0,
    ) -> dict:
        """Make an authenticated request. Retries on 429 only."""
        if self._client is None:
            raise RuntimeError(
                "FrameIOClient is not open. Use `async with FrameIOClient(token) as c:`"
            )

        response = await self._client.request(method, path, params=params, json=json)

        # A 401 is deliberately not retried. It means the token is bad, and only the
        # caller can fix that. Retrying here previously masked entitlement failures.

        # 429 → exponential backoff, retry up to MAX_RETRIES
        if response.status_code == 429:
            if retry_count >= self.MAX_RETRIES:
                raise RateLimitError(
                    f"Rate limit exceeded, retries exhausted for {method} {path}"
                )
            wait_seconds = 2 ** retry_count
            await asyncio.sleep(wait_seconds)
            return await self._request(
                method, path, params=params, json=json, retry_count=retry_count + 1
            )

        if response.status_code >= 400:
            try:
                error_body = response.json()
                error_detail = jsonlib.dumps(
                    error_body.get("errors", error_body), indent=2
                )
            except Exception:
                error_detail = response.text[:500]
            raise FrameIOError(
                f"Frame.io API error ({response.status_code}) for {method} {path}:\n{error_detail}"
            )

        if response.status_code == 204:
            return {}

        return response.json()

    # ---------------------------------------------------------------
    # User / Account
    # ---------------------------------------------------------------

    async def get_me(self) -> dict:
        """Return the authenticated user's info."""
        return (await self._request("GET", "/me")).get("data", {})

    async def list_accounts(self) -> list[dict]:
        """List all accounts the user has access to."""
        result = await self._request("GET", "/accounts")
        return result.get("data", [])

    # ---------------------------------------------------------------
    # Files / Folders
    # ---------------------------------------------------------------

    async def get_file(self, account_id: str, file_id: str) -> dict:
        """Retrieve a single file's metadata (including parent_id, name, media_type)."""
        return (await self._request(
            "GET", f"/accounts/{account_id}/files/{file_id}"
        )).get("data", {})

    async def list_folder_children(
        self,
        account_id: str,
        folder_id: str,
        page_size: int = 100,
        after: str | None = None,
    ) -> dict:
        """List a folder's direct children. Returns {data: [...], links: {next?}}."""
        params: dict[str, Any] = {"page_size": page_size}
        if after:
            params["after"] = after
        return await self._request(
            "GET", f"/accounts/{account_id}/folders/{folder_id}/children", params=params
        )

    async def download_file_content(self, download_url: str) -> bytes:
        """Fetch file bytes from a presigned Frame.io URL. Presigned URLs are unauthenticated."""
        async with httpx.AsyncClient(timeout=120.0) as raw:
            resp = await raw.get(download_url)
            resp.raise_for_status()
            return resp.content

    # ---------------------------------------------------------------
    # Comments
    # ---------------------------------------------------------------

    async def create_comment(
        self,
        account_id: str,
        file_id: str,
        text: str,
        timestamp_microseconds: int | None = None,
        duration_microseconds: int | None = None,
    ) -> dict:
        """Post a comment on a file. `timestamp_microseconds` anchors it to a specific frame."""
        payload: dict[str, Any] = {"data": {"text": text}}
        if timestamp_microseconds is not None:
            payload["data"]["timestamp"] = timestamp_microseconds
        if duration_microseconds is not None:
            payload["data"]["duration"] = duration_microseconds
        return (await self._request(
            "POST", f"/accounts/{account_id}/files/{file_id}/comments", json=payload
        )).get("data", {})

    async def list_comments(
        self,
        account_id: str,
        file_id: str,
        page_size: int = 50,
        after: str | None = None,
    ) -> dict:
        """List comments on a file. Returns {data: [...], links: {next?}, total_count?}."""
        params: dict[str, Any] = {"page_size": page_size, "include_total_count": "true"}
        if after:
            params["after"] = after
        return await self._request(
            "GET", f"/accounts/{account_id}/files/{file_id}/comments", params=params
        )

    # ---------------------------------------------------------------
    # Comment attachments
    # ---------------------------------------------------------------
    # NOTE: The exact attachment endpoint structure needs verification against
    # Frame.io v4 OpenAPI spec at https://api.frame.io/v4/openapi.json — this
    # implementation is the best guess based on Frame.io v4 patterns. Adjust if
    # live testing surfaces a different shape.

    async def create_comment_attachment(
        self,
        account_id: str,
        comment_id: str,
        file_name: str,
        media_type: str,
        file_size: int,
    ) -> dict:
        """Step 1 of attachment upload: create the record. Returns a presigned upload URL."""
        payload = {
            "data": {
                "name": file_name,
                "media_type": media_type,
                "file_size": file_size,
            }
        }
        return (await self._request(
            "POST",
            f"/accounts/{account_id}/comments/{comment_id}/attachments",
            json=payload,
        )).get("data", {})

    @staticmethod
    async def upload_to_presigned_url(
        url: str, file_bytes: bytes, media_type: str
    ) -> None:
        """Step 2 of attachment upload: PUT bytes to the presigned URL Frame.io returned."""
        async with httpx.AsyncClient(timeout=300.0) as raw:
            resp = await raw.put(
                url,
                content=file_bytes,
                headers={"Content-Type": media_type},
            )
            resp.raise_for_status()
