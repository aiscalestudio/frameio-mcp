"""List comments on a Frame.io file, with optional 'mine only' filter."""

from __future__ import annotations

from ..client import FrameIOClient
from ..config import Config


def _format_comment(c: dict) -> dict:
    """Normalize a Frame.io comment object for LLM readability."""
    ts_us = c.get("timestamp")
    dur_us = c.get("duration")
    return {
        "comment_id": c.get("id"),
        "text": c.get("text"),
        "timestamp_microseconds": ts_us,
        "timestamp_seconds": (ts_us / 1_000_000) if ts_us is not None else None,
        "duration_seconds": (dur_us / 1_000_000) if dur_us else None,
        "creator_id": c.get("creator_id") or c.get("owner_id"),
        "created_at": c.get("created_at"),
        "attachments": c.get("attachments") or [],
    }


async def list_comments(
    account_id: str,
    file_id: str,
    page_size: int = 50,
    after: str | None = None,
    only_mine: bool = False,
) -> dict:
    """List comments on a file. Cursor-paginated. Optionally filter to current user."""
    if not (1 <= page_size <= 100):
        raise ValueError(f"page_size must be between 1 and 100, got {page_size}")

    config = Config.from_env()
    async with FrameIOClient(config) as client:
        result = await client.list_comments(
            account_id=account_id,
            file_id=file_id,
            page_size=page_size,
            after=after,
        )
        raw_comments = result.get("data", [])

        if only_mine:
            me = await client.get_me()
            user_id = me.get("id")
            raw_comments = [
                c for c in raw_comments
                if (c.get("creator_id") == user_id or c.get("owner_id") == user_id)
            ]

    return {
        "comments": [_format_comment(c) for c in raw_comments],
        "next_cursor": (result.get("links") or {}).get("next"),
        "total_count": result.get("total_count"),
    }
