"""Tests for the tool layer: unit conversion, filtering, and account resolution.

These functions sit between the LLM and the API, so their job is translation. The
translations that matter are microseconds to seconds (Frame.io speaks microseconds,
humans and LLMs speak seconds) and Frame.io's raw comment shape to something readable.
Both are silent-corruption risks: a wrong conversion produces a plausible number that
puts a review note at the wrong point in a video.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from frameio_mcp.config import FRAMEIO_API_BASE_URL
from frameio_mcp.tools.get_asset_from_url import get_asset_from_url
from frameio_mcp.tools.list_comments import list_comments
from frameio_mcp.tools.post_comment import post_comment

TOKEN = "token-abc"
COMMENTS_URL = f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1/comments"


class TestPostComment:
    @respx.mock
    async def test_converts_seconds_to_microseconds(self):
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 1.5)

        import json as jsonlib

        body = jsonlib.loads(route.calls.last.request.content)
        assert body["data"]["timestamp"] == 1_500_000

    @respx.mock
    async def test_rounds_rather_than_truncates(self):
        """Truncating would drift a comment earlier by up to a microsecond each time."""
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 0.0000015)

        import json as jsonlib

        body = jsonlib.loads(route.calls.last.request.content)
        assert body["data"]["timestamp"] == 2

    @respx.mock
    async def test_omits_duration_when_not_supplied(self):
        """Sending duration=0 would turn a point comment into a zero-length range."""
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 5.0)

        import json as jsonlib

        body = jsonlib.loads(route.calls.last.request.content)
        assert "duration" not in body["data"]

    @respx.mock
    async def test_sends_duration_for_range_comments(self):
        route = respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "c1"}})
        )

        await post_comment(TOKEN, "acct-1", "file-1", "note", 5.0, 2.5)

        import json as jsonlib

        body = jsonlib.loads(route.calls.last.request.content)
        assert body["data"]["duration"] == 2_500_000

    async def test_rejects_negative_timestamp(self):
        with pytest.raises(ValueError, match=">= 0"):
            await post_comment(TOKEN, "acct-1", "file-1", "note", -1.0)

    @respx.mock
    async def test_reports_the_timestamp_frameio_actually_stored(self):
        """Frame.io may quantise to a frame boundary; the caller needs the real value."""
        respx.post(COMMENTS_URL).mock(
            return_value=httpx.Response(
                201, json={"data": {"id": "c1", "timestamp": 1_000_000}}
            )
        )

        result = await post_comment(TOKEN, "acct-1", "file-1", "note", 1.234)

        assert result["timestamp_seconds"] == 1.0


class TestListComments:
    @respx.mock
    async def test_converts_microseconds_to_seconds(self):
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "c1", "text": "hi", "timestamp": 2_500_000}]},
            )
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert result["comments"][0]["timestamp_seconds"] == 2.5

    @respx.mock
    async def test_preserves_a_zero_timestamp(self):
        """A comment at 00:00 is real; treating 0 as absent would hide it."""
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "c1", "timestamp": 0}]}
            )
        )

        result = await list_comments(TOKEN, "acct-1", "file-1")

        assert result["comments"][0]["timestamp_seconds"] == 0

    @respx.mock
    async def test_only_mine_filters_by_the_authenticated_user(self):
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
        """Fetching identity when it is not needed doubles the API calls per listing."""
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        me_route = respx.get(f"{FRAMEIO_API_BASE_URL}/me").mock(
            return_value=httpx.Response(200, json={"data": {"id": "me"}})
        )

        await list_comments(TOKEN, "acct-1", "file-1")

        assert me_route.call_count == 0

    @respx.mock
    async def test_surfaces_the_pagination_cursor(self):
        respx.get(COMMENTS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": [], "links": {"next": "cursor-2"}, "total_count": 120},
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
    async def test_resolves_a_file_and_converts_duration(self):
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "acct-1"}]})
        )
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "name": "cut.mp4",
                        "media_type": "video",
                        "duration": 90_000_000,
                    }
                },
            )
        )

        result = await get_asset_from_url(
            TOKEN, "https://next.frame.io/project/proj-1/view/file-1"
        )

        assert result["file_id"] == "file-1"
        assert result["account_id"] == "acct-1"
        assert result["duration_seconds"] == 90.0

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
            return_value=httpx.Response(200, json={"data": {"name": "cut.mp4"}})
        )

        result = await get_asset_from_url(
            TOKEN, "https://next.frame.io/player/file-1"
        )

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
        respx.get(f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/files/file-1").mock(
            return_value=httpx.Response(404, json={"errors": ["not found"]})
        )

        with pytest.raises(ValueError, match="not accessible"):
            await get_asset_from_url(TOKEN, "https://next.frame.io/player/file-1")
