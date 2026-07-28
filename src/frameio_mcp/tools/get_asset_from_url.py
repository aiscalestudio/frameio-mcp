"""Resolve a Frame.io URL to identifiers (account_id, project_id, file_id, ...)."""

from __future__ import annotations

from urllib.parse import urlparse

from ..client import FrameIOClient, FrameIOError
from ..config import Config


def parse_frameio_url(url: str) -> dict:
    """Extract identifiers from a Frame.io URL by pattern-matching the path segments.

    Supported patterns:
      https://next.frame.io/project/{project_id}/view/{file_id}
      https://next.frame.io/player/{file_id}
      https://app.frame.io/reviews/{review_id}/{file_id}
      https://app.frame.io/assets/{file_id}
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    result: dict = {}

    for i, part in enumerate(parts):
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        nxt2 = parts[i + 2] if i + 2 < len(parts) else None
        if part == "project" and nxt:
            result["project_id"] = nxt
        elif part == "view" and nxt:
            result["file_id"] = nxt
        elif part == "player" and nxt:
            result["file_id"] = nxt
        elif part == "assets" and nxt:
            result["file_id"] = nxt
        elif part == "reviews" and nxt and nxt2:
            result["review_id"] = nxt
            result["file_id"] = nxt2

    return result


async def get_asset_from_url(url: str) -> dict:
    """Resolve a Frame.io URL by parsing it, then hydrating file metadata via the API."""
    parsed = parse_frameio_url(url)

    if not parsed.get("file_id"):
        raise ValueError(
            f"Could not extract a file_id from URL: {url}\n\n"
            f"Supported URL patterns:\n"
            f"  https://next.frame.io/project/{{project_id}}/view/{{file_id}}\n"
            f"  https://next.frame.io/player/{{file_id}}\n"
            f"  https://app.frame.io/reviews/{{review_id}}/{{file_id}}"
        )

    file_id = parsed["file_id"]
    config = Config.from_env()

    async with FrameIOClient(config) as client:
        accounts = await client.list_accounts()
        if not accounts:
            raise ValueError(
                "No Frame.io accounts accessible with current credentials. "
                "Check your Adobe Developer Console project setup."
            )

        # Try each account until the file resolves
        file_data: dict | None = None
        used_account_id: str | None = None
        last_error: Exception | None = None
        for account in accounts:
            account_id = account.get("id")
            if not account_id:
                continue
            try:
                file_data = await client.get_file(account_id, file_id)
                used_account_id = account_id
                break
            except FrameIOError as e:
                last_error = e
                continue

        if file_data is None:
            raise ValueError(
                f"File {file_id} not accessible in any of the user's accounts "
                f"({[a.get('name') for a in accounts]}). "
                f"Check the URL and your permissions. Last error: {last_error}"
            )

    duration_us = file_data.get("duration")
    duration_seconds = (duration_us / 1_000_000) if duration_us else None

    project = file_data.get("project") or {}
    return {
        "account_id": used_account_id,
        "workspace_id": file_data.get("workspace_id") or project.get("workspace_id"),
        "project_id": parsed.get("project_id") or file_data.get("project_id") or project.get("id"),
        "file_id": file_id,
        "file_name": file_data.get("name"),
        "media_type": file_data.get("media_type"),
        "duration_seconds": duration_seconds,
    }
