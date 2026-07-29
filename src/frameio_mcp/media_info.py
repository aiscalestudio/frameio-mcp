"""Frame rate lookup and timestamp conversion for Frame.io v4.

Frame.io v4 stores comment timestamps in **frames**, not microseconds or milliseconds.
Verified against a live 29.97 fps file: a comment stored as 11974 displays in the
Frame.io UI as `00:06:39;16`, and 11974 / 29.97 = 399.53 s = 6:39.53. The reverse
calculation agrees exactly, as does a second sample (9000 -> `00:05:00;10`).

This is a nastier class of bug than a normal unit error, because a wrong conversion
still writes successfully. The comment simply lands somewhere else in the video and
nobody is shown an error. The original implementation multiplied seconds by 1,000,000,
which placed a comment requested at 9 seconds about five minutes in.

Frame rate is per-file and is only returned when metadata is explicitly requested via
`?include=metadata`. When it cannot be determined, these helpers raise rather than
assume a default, because assuming means silently writing review notes to the wrong
timecode.
"""

from __future__ import annotations

from typing import Any

FRAME_RATE_FIELD = "Frame Rate"
DURATION_FIELD = "Duration"


class MediaInfoError(ValueError):
    """Raised when media information needed for a conversion is missing or unusable."""


def metadata_fields(file_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten Frame.io's metadata list into a name -> value mapping.

    The API returns a list of field objects rather than a dict, e.g.
    `[{"field_definition_name": "Frame Rate", "value": 29.97}, ...]`.
    """
    fields = file_data.get("metadata") or []
    if not isinstance(fields, list):
        return {}
    return {
        field["field_definition_name"]: field.get("value")
        for field in fields
        if isinstance(field, dict) and field.get("field_definition_name")
    }


def _numeric_field(file_data: dict[str, Any], name: str) -> float | None:
    value = metadata_fields(file_data).get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def frame_rate_of(file_data: dict[str, Any]) -> float | None:
    """The file's frame rate, or None if metadata was not requested or is absent."""
    return _numeric_field(file_data, FRAME_RATE_FIELD)


def duration_seconds_of(file_data: dict[str, Any]) -> float | None:
    """The file's duration in seconds, or None if unavailable.

    Note this comes from metadata. The plain file endpoint has no `duration` field at
    all, which is why it previously always reported None.
    """
    return _numeric_field(file_data, DURATION_FIELD)


def _require_frame_rate(frame_rate: float | None) -> float:
    if frame_rate is None or frame_rate <= 0:
        raise MediaInfoError(
            f"Cannot convert a timestamp without a valid frame rate (got {frame_rate!r}). "
            f"Frame.io returns it in file metadata; request the file with "
            f"include_metadata=True. Refusing to guess, because a wrong frame rate "
            f"places the comment at the wrong point in the video with no error shown."
        )
    return float(frame_rate)


def seconds_to_frames(seconds: float, frame_rate: float | None) -> int:
    """Convert playback seconds to a Frame.io frame number.

    Rounds to the nearest frame. Truncating would drift every comment slightly earlier.
    """
    fps = _require_frame_rate(frame_rate)
    if seconds < 0:
        raise MediaInfoError(f"Timestamp cannot be negative, got {seconds}")
    return int(round(seconds * fps))


def frames_to_seconds(frames: float, frame_rate: float | None) -> float:
    """Convert a Frame.io frame number to playback seconds."""
    fps = _require_frame_rate(frame_rate)
    return frames / fps
