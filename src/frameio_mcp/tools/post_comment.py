"""Post a frame-accurate comment on a Frame.io file."""

from __future__ import annotations

from typing import Optional

from ..client import FrameIOClient
from ..config import Config


async def post_comment(
    account_id: str,
    file_id: str,
    text: str,
    timestamp_seconds: float,
    duration_seconds: Optional[float] = None,
) -> dict:
    """Create a Frame.io comment. Converts fractional seconds to microseconds for the API."""
    if timestamp_seconds < 0:
        raise ValueError(f"timestamp_seconds must be >= 0, got {timestamp_seconds}")
    timestamp_us = int(round(timestamp_seconds * 1_000_000))
    duration_us = int(round(duration_seconds * 1_000_000)) if duration_seconds else None

    config = Config.from_env()
    async with FrameIOClient(config) as client:
        result = await client.create_comment(
            account_id=account_id,
            file_id=file_id,
            text=text,
            timestamp_microseconds=timestamp_us,
            duration_microseconds=duration_us,
        )

    comment_id = result.get("id")
    stored_ts = result.get("timestamp")
    return {
        "comment_id": comment_id,
        "text": result.get("text"),
        "timestamp_microseconds": stored_ts,
        "timestamp_seconds": (stored_ts / 1_000_000) if stored_ts else timestamp_seconds,
        "created_at": result.get("created_at"),
        "url": f"https://next.frame.io/project/comments/{comment_id}" if comment_id else None,
    }
