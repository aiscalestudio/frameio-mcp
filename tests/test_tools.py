"""Tests for the tool layer: timestamp conversion, filtering, and account resolution.

These translations are the silent-corruption risk in this codebase. Frame.io v4 stores
comment positions as **frame numbers**, so converting them needs the file's frame rate.
A wrong conversion posts successfully and puts the note somewhere else in the video, with
no error shown to anyone.

An earlier version of this file asserted a microsecond conversion and passed, because it
checked that the code did what the code did rather than what Frame.io actually stores.
The fixtures here use values measured from a live 29.97 fps file: a comment stored as
11974 displays as `00:06:39;16`, and 9000 displays as `00:05:00;10`.
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest
import respx

from frameio_mcp.config import FRAMEIO_API_BASE_URL
from frameio_mcp.media_info import MediaInfoError
from frameio_mcp.tools.get_asset_from_url import get_asset_from_url
from frameio_mcp.tools.list_comments import list_comments
from frameio_mcp.tools.post_comment import post_comment

TOKEN = "token-abc"
FILE_URL = f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1"
COMMENTS_URL = f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"
FPS = 29.97


def mock_file(frame_rate: float | None = FPS, duration: float | None = 1734.066):
    """Mock the file endpoint with a metadata block shaped like the real one."""
    metadata = []
    if frame_rate is not None:
        metadata.append({"field_definition_name": "Frame Rate", "value": frame_rate})
    if duration is not None:
        metadata.append({"field_definition_name": "Duration", "value": duration})
    return respx.get(FILE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "name": "clip.mp4",
                    "media_type": "video/mp4",
                    "parent_id": "folder-1",
                    "metadata": metadata,
                }
            },
        )
    )


def posted_body(route) -> dict:
    return jsonlib.loads(route.calls.last.request.content)["data"]


class TestPostComment:
    @respx.mock
    async def test_converts_seconds_to_frames(self):
        """9 seconds on a 29.97 fps video is frame 270, not 9,000,000."""
        mock_file()
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 9.0)

        assert posted_body(route)["timestamp"] == 270

    @respx.mock
    async def test_matches_a_real_observed_position(self):
        """00:06:39;16 on the live file is stored as 11974."""
        mock_file()
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 6 * 60 + 39 + 16 / FPS)

        assert posted_body(route)["timestamp"] == 11974

    @respx.mock
    async def test_omits_duration_when_not_supplied(self):
        """Sending 0 would turn a point comment into a zero-length range."""
        mock_file()
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 5.0)

        assert "duration" not in posted_body(route)

    @respx.mock
    async def test_range_duration_is_also_in_frames(self):
        mock_file()
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 5.0, 2.0)

        assert posted_body(route)["duration"] == round(2.0 * FPS)

    async def test_rejects_negative_timestamp(self):
        with pytest.raises(ValueError, match=">= 0"):
            await post_comment(TOKEN, "acct-1", "file-1", "note", -1.0)

    @respx.mock
    async def test_rejects_a_timestamp_past_the_end_of_the_video(self):
        """Frame.io rejects these too, but with an opaque message."""
        mock_file(duration=50.0)

        with pytest.raises(ValueError, match="past the end"):
            await post_comment(TOKEN, "acct-1", "file-1", "note", 600.0)

    @respx.mock
    async def test_refuses_to_post_without_a_frame_rate(self):
        """Guessing a frame rate silently places the comment at the wrong timecode."""
        mock_file(frame_rate=None)

        with pytest.raises(MediaInfoError, match="frame rate"):
            await post_comment(TOKEN, "acct-1", "file-1", "note", 9.0)

    @respx.mock
    async def test_reports_the_position_frameio_actually_stored(self):
        """Frame.io may snap to a frame boundary; the caller needs the real value."""
        mock_file()
        respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1", "timestamp": 300}})
        )

        result = await post_comment(TOKEN, "acct-1", "file-1", "note", 9.0)

        assert result["timestamp_frames"] == 300
        assert round(result["timestamp_seconds"], 2) == 10.01
        assert result["frame_rate"] == FPS


class TestListComments:
    @respx.mock
    async def test_converts_frames_to_seconds(self):
        mock_file()
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "c1", "text": "hi", "timestamp": 11974}]}
            )
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert round(result["comments"][0]["timestamp_seconds"], 2) == 399.53
        assert result["comments"][0]["timestamp_frames"] == 11974

    @respx.mock
    async def test_preserves_a_zero_timestamp(self):
        """A comment at the very start is real; treating 0 as absent would hide it."""
        mock_file()
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"id": "c1", "timestamp": 0}]})
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert result["comments"][0]["timestamp_seconds"] == 0

    @respx.mock
    async def test_reports_frames_without_seconds_when_frame_rate_is_missing(self):
        """Better to omit seconds than to report frame counts as if they were seconds."""
        mock_file(frame_rate=None)
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"id": "c1", "timestamp": 900}]})
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert result["comments"][0]["timestamp_frames"] == 900
        assert result["comments"][0]["timestamp_seconds"] is None

    @respx.mock
    async def test_only_mine_filters_by_the_authenticated_user(self):
        mock_file()
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "c1", "creator_id": "me"},
                        {"id": "c2", "creator_id": "someone-else"},
                    ]
                },
            )
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "me"}})
        )

        result = await list_comments(TOKEN, "acct-1", "file-1", only_mine=True)

        assert [c["comment_id"] for c in result["comments"]] == ["c1"]

    @respx.mock
    async def test_without_only_mine_no_identity_call_is_made(self):
        mock_file()
        respx.get(COMMENTS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        me_route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "me"}})
        )

        await list_comments(TOKEN, "acct-1", "file-1")

        assert me_route.call_count == 0

    @respx.mock
    async def test_surfaces_the_pagination_cursor(self):
        mock_file()
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [], "links": {"next": "cursor-2"}, "total_count": 120}
            )
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert result["next_cursor"] == "cursor-2"
        assert result["total_count"] == 120

    @pytest.mark.parametrize("page_size", [0, 101, -1])
    async def test_rejects_out_of_range_page_size(self, page_size):
        with pytest.raises(ValueError, match="between 1 and 100"):
            await list_comments(TOKEN, "acct-1", "file-1", page_size=page_size)


class TestGetAssetFromUrl:
    @respx.mock
    async def test_reports_duration_and_frame_rate_from_metadata(self):
        """The plain file endpoint has no duration field, so this needs metadata."""
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "acct-1"}]})
        )
        mock_file()

        result = await get_asset_from_url(
            TOKEN, "https://next.frame.io/project/proj-1/view/file-1"
        )

        assert result["file_id"] == "file-1"
        assert result["account_id"] == "acct-1"
        assert result["duration_seconds"] == 1734.066
        assert result["frame_rate"] == FPS

    @respx.mock
    async def test_tries_every_account_until_the_file_resolves(self):
        """A user in several Frame.io accounts gets a 404 from the wrong ones."""
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "acct-wrong"}, {"id": "acct-right"}]}
            )
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts/acct-wrong/files/file-1").mock(
            return_value=httpx.Response(404, json={"errors": ["not found"]})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts/acct-right/files/file-1").mock(
            return_value=httpx.Response(200, json={"data": {"name": "clip.mp4"}})
        )

        result = await get_asset_from_url(TOKEN, "https://next.frame.io/player/file-1")

        assert result["account_id"] == "acct-right"

    async def test_unparseable_url_fails_before_any_api_call(self):
        with pytest.raises(ValueError, match="file_id"):
            await get_asset_from_url(TOKEN, "https://next.frame.io/dashboard")

    @respx.mock
    async def test_no_accessible_accounts_is_reported_clearly(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        with pytest.raises(ValueError, match="No Frame.io accounts"):
            await get_asset_from_url(TOKEN, "https://next.frame.io/player/file-1")

    @respx.mock
    async def test_file_missing_from_every_account_names_the_accounts(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "acct-1", "name": "Studio"}]}
            )
        )
        respx.get(FILE_URL).mock(
            return_value=httpx.Response(404, json={"errors": ["not found"]})
        )

        with pytest.raises(ValueError, match="not accessible"):
            await get_asset_from_url(TOKEN, "https://next.frame.io/player/file-1")
