"""Post a frame-accurate comment on a Frame.io file."""

from __future__ import annotations

from ..client import FrameIOClient
from ..media_info import MediaInfoError, duration_seconds_of, frame_rate_of, seconds_to_frames


async def post_comment(
    access_token: str,
    account_id: str,
    file_id: str,
    text: str,
    timestamp_seconds: float,
    duration_seconds: float | None = None,
) -> dict:
    """Create a Frame.io comment at a given playback position.

    Frame.io v4 expresses comment timestamps in frames, so the file's frame rate is
    fetched first. Getting this wrong does not raise: the comment posts successfully and
    simply lands at the wrong point in the video, which is why the frame rate is required
    rather than defaulted.
    """
    if timestamp_seconds < 0:
        raise ValueError(f"timestamp_seconds must be >= 0, got {timestamp_seconds}")

    async with FrameIOClient(access_token) as client:
        media = await client.get_file(account_id, file_id, include_metadata=True)
        frame_rate = frame_rate_of(media)
        duration = duration_seconds_of(media)

        if frame_rate is None:
            raise MediaInfoError(
                f"Frame.io did not report a frame rate for '{media.get('name', file_id)}', "
                f"so the comment position cannot be calculated. This usually means the "
                f"file is not a video, or Frame.io has not finished processing it."
            )

        if duration is not None and timestamp_seconds > duration:
            raise ValueError(
                f"timestamp_seconds {timestamp_seconds} is past the end of "
                f"'{media.get('name')}', which is {duration:.1f}s long."
            )

        timestamp_frames = seconds_to_frames(timestamp_seconds, frame_rate)
        duration_frames = (
            seconds_to_frames(duration_seconds, frame_rate)
            if duration_seconds
            else None
        )

        result = await client.create_comment(
            account_id=account_id,
            file_id=file_id,
            text=text,
            timestamp_frames=timestamp_frames,
            duration_frames=duration_frames,
        )

    comment_id = result.get("id")
    stored_frames = result.get("timestamp")
    return {
        "comment_id": comment_id,
        "text": result.get("text"),
        "timestamp_frames": stored_frames,
        "timestamp_seconds": (
            stored_frames / frame_rate if stored_frames is not None else timestamp_seconds
        ),
        "frame_rate": frame_rate,
        "created_at": result.get("created_at"),
        "url": f"https://next.frame.io/project/comments/{comment_id}" if comment_id else None,
    }
