"""List comments on a Frame.io file, with optional 'mine only' filter."""

from __future__ import annotations

from ..client import FrameIOClient
from ..media_info import frame_rate_of, frames_to_seconds


def _format_comment(c: dict, frame_rate: float | None) -> dict:
    """Normalize a Frame.io comment for readability.

    Frame.io stores `timestamp` and `duration` as frame counts, so seconds are only
    derivable with the file's frame rate. When it is unavailable the frame numbers are
    still returned and the seconds are left None, rather than reporting a number that
    looks like seconds but is not.
    """
    ts_frames = c.get("timestamp")
    dur_frames = c.get("duration")
    to_seconds = (
        (lambda v: round(frames_to_seconds(v, frame_rate), 3))
        if frame_rate
        else (lambda v: None)
    )
    return {
        "comment_id": c.get("id"),
        "text": c.get("text"),
        "timestamp_frames": ts_frames,
        "timestamp_seconds": to_seconds(ts_frames) if ts_frames is not None else None,
        "duration_seconds": to_seconds(dur_frames) if dur_frames else None,
        "creator_id": c.get("creator_id") or c.get("owner_id"),
        "created_at": c.get("created_at"),
        "attachments": c.get("attachments") or [],
    }


async def list_comments(
    access_token: str,
    account_id: str,
    file_id: str,
    page_size: int = 50,
    after: str | None = None,
    only_mine: bool = False,
) -> dict:
    """List comments on a file. Cursor-paginated. Optionally filter to current user."""
    if not (1 <= page_size <= 100):
        raise ValueError(f"page_size must be between 1 and 100, got {page_size}")

    async with FrameIOClient(access_token) as client:
        media = await client.get_file(account_id, file_id, include_metadata=True)
        frame_rate = frame_rate_of(media)

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
        "comments": [_format_comment(c, frame_rate) for c in raw_comments],
        "frame_rate": frame_rate,
        "next_cursor": (result.get("links") or {}).get("next"),
        "total_count": result.get("total_count"),
    }
