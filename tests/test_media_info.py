"""Tests for frame-rate handling and timestamp conversion.

Frame.io v4 stores comment timestamps in **frames**, not microseconds or milliseconds.
Verified against a live file: stored 11974 on a 29.97 fps video displays as
`00:06:39;16`, and 11974 / 29.97 = 399.53 s = 6:39.53. Reverse check agrees exactly.

This matters more than a normal unit bug. A wrong conversion still writes successfully,
so the comment lands at a plausible-looking but wrong point in the video, and nobody
sees an error. The original code multiplied seconds by 1,000,000, which put a comment
requested at 9 seconds roughly five minutes in.

Frame rate is per-file and only returned when metadata is explicitly requested, so a
missing frame rate must fail loudly rather than fall back to a guess.
"""

from __future__ import annotations

import pytest

from frameio_mcp.media_info import (
    MediaInfoError,
    duration_seconds_of,
    frame_rate_of,
    frames_to_seconds,
    metadata_fields,
    seconds_to_frames,
)

# Shaped like a real Frame.io v4 `include=metadata` response.
REAL_METADATA = {
    "name": "VIDEO1_Savannagh Sellecke_04.mp4",
    "media_type": "video/mp4",
    "metadata": [
        {"field_definition_name": "Visual Bit Depth", "value": 8.0},
        {"field_definition_name": "Frame Rate", "value": 29.97},
        {"field_definition_name": "Duration", "value": 1734.066},
        {"field_definition_name": "Name", "value": "VIDEO1_Savannagh Sellecke_04.mp4"},
    ],
}


class TestMetadataExtraction:
    def test_reads_frame_rate(self):
        assert frame_rate_of(REAL_METADATA) == 29.97

    def test_reads_duration(self):
        assert duration_seconds_of(REAL_METADATA) == 1734.066

    def test_flattens_fields_by_name(self):
        assert metadata_fields(REAL_METADATA)["Frame Rate"] == 29.97

    def test_absent_metadata_yields_none(self):
        """The plain file endpoint returns no metadata at all unless asked."""
        assert frame_rate_of({"name": "clip.mp4"}) is None
        assert duration_seconds_of({"name": "clip.mp4"}) is None

    def test_non_numeric_frame_rate_is_ignored(self):
        payload = {"metadata": [{"field_definition_name": "Frame Rate", "value": "n/a"}]}
        assert frame_rate_of(payload) is None


class TestSecondsToFrames:
    def test_converts_at_2997(self):
        """9 seconds on a 29.97 fps video is frame 270, not 9,000,000."""
        assert seconds_to_frames(9.0, 29.97) == 270

    def test_matches_a_real_observed_value(self):
        """00:06:39;16 on the live file is stored as 11974."""
        assert seconds_to_frames(6 * 60 + 39 + 16 / 29.97, 29.97) == 11974

    def test_zero_stays_zero(self):
        """A comment at the very start is legitimate and must not be shifted."""
        assert seconds_to_frames(0.0, 29.97) == 0

    def test_rounds_to_the_nearest_frame(self):
        """Truncating would drift every comment slightly earlier."""
        assert seconds_to_frames(1.0, 30.0) == 30
        assert seconds_to_frames(0.99, 30.0) == 30

    @pytest.mark.parametrize("fps", [0, -1, None])
    def test_refuses_an_unusable_frame_rate(self, fps):
        """Guessing here writes a comment to the wrong place with no error shown."""
        with pytest.raises(MediaInfoError, match="frame rate"):
            seconds_to_frames(5.0, fps)

    def test_refuses_a_negative_timestamp(self):
        with pytest.raises(MediaInfoError, match="negative"):
            seconds_to_frames(-1.0, 29.97)


class TestFramesToSeconds:
    def test_converts_at_2997(self):
        assert round(frames_to_seconds(9000, 29.97), 2) == 300.3

    def test_zero_stays_zero(self):
        assert frames_to_seconds(0, 29.97) == 0.0

    def test_round_trip_is_stable(self):
        for seconds in (0.0, 1.0, 9.0, 399.53, 1700.0):
            frames = seconds_to_frames(seconds, 29.97)
            assert abs(frames_to_seconds(frames, 29.97) - seconds) < 0.05

    @pytest.mark.parametrize("fps", [0, -1, None])
    def test_refuses_an_unusable_frame_rate(self, fps):
        with pytest.raises(MediaInfoError, match="frame rate"):
            frames_to_seconds(100, fps)
