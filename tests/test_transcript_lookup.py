"""Tests for locating a video's sibling transcript file.

This is the most failure-prone tool, because Frame.io has no transcript API and the
workaround depends on an editor having manually uploaded an SRT next to the video.
Every branch here ends in a human doing something, so the errors have to say what.

Transcript *parsing* is covered in test_transcript_parsing.py. This file covers the
lookup: which file gets chosen, and what happens when the choice is ambiguous.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from frameio_mcp.config import FRAMEIO_API_BASE_URL
from frameio_mcp.tools.get_transcript_from_sibling import (
    _extract_download_url,
    get_transcript_from_sibling,
)

TOKEN = "token-abc"
ACCOUNT = "acct-1"
VIDEO_ID = "vid-1"
FILES = f"{FRAMEIO_API_BASE_URL}/accounts/{ACCOUNT}/files"
CHILDREN = f"{FRAMEIO_API_BASE_URL}/accounts/{ACCOUNT}/folders/folder-1/children"

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"


def mock_video(name: str = "interview.mp4"):
    respx.get(f"{FILES}/{VIDEO_ID}").mock(
        return_value=httpx.Response(
            200, json={"data": {"name": name, "parent_id": "folder-1"}}
        )
    )


def mock_folder(children: list[dict], next_cursor: str | None = None):
    respx.get(CHILDREN).mock(
        return_value=httpx.Response(
            200, json={"data": children, "links": {"next": next_cursor}}
        )
    )


def mock_transcript_download(file_id: str, content: str = SRT):
    respx.get(f"{FILES}/{file_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "name": f"{file_id}.srt",
                    "download_url": "https://cdn.frame.io/transcript.srt",
                }
            },
        )
    )
    respx.get("https://cdn.frame.io/transcript.srt").mock(
        return_value=httpx.Response(200, content=content.encode())
    )


class TestDownloadUrlExtraction:
    """Frame.io returns the download URL in several shapes depending on the endpoint."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"download_url": "https://x/f.srt"},
            {"url": "https://x/f.srt"},
            {"original_url": "https://x/f.srt"},
            {"downloads": {"original": "https://x/f.srt"}},
            {"media_links": {"original": {"download_url": "https://x/f.srt"}}},
        ],
    )
    def test_finds_the_url_in_each_known_shape(self, payload):
        assert _extract_download_url(payload) == "https://x/f.srt"

    def test_returns_none_when_absent(self):
        assert _extract_download_url({"name": "f.srt"}) is None


class TestSiblingSelection:
    @respx.mock
    async def test_prefers_the_transcript_matching_the_video_name(self):
        """A folder often holds transcripts for several videos."""
        mock_video("interview.mp4")
        mock_folder(
            [
                {"id": "other", "name": "b-roll.srt"},
                {"id": "match", "name": "interview.srt"},
            ]
        )
        mock_transcript_download("match")

        result = await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

        assert result["source_file_id"] == "match"
        assert result["transcript"][0]["text"] == "Hello there"

    @respx.mock
    async def test_falls_back_to_a_lone_candidate_with_a_different_name(self):
        """Editors rename exports; one unambiguous file is better than an error."""
        mock_video("interview.mp4")
        mock_folder([{"id": "only", "name": "final-transcript.srt"}])
        mock_transcript_download("only")

        result = await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

        assert result["source_file_id"] == "only"

    @respx.mock
    async def test_ambiguous_candidates_raise_rather_than_guess(self):
        """Picking arbitrarily would attach review notes to the wrong video's words."""
        mock_video("interview.mp4")
        mock_folder(
            [
                {"id": "a", "name": "take-one.srt"},
                {"id": "b", "name": "take-two.srt"},
            ]
        )

        with pytest.raises(ValueError, match="Multiple transcript files"):
            await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

    @respx.mock
    async def test_accepts_vtt_as_well_as_srt(self):
        mock_video("interview.mp4")
        mock_folder([{"id": "match", "name": "interview.vtt"}])
        respx.get(f"{FILES}/match").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "name": "interview.vtt",
                        "download_url": "https://cdn.frame.io/t.vtt",
                    }
                },
            )
        )
        respx.get("https://cdn.frame.io/t.vtt").mock(
            return_value=httpx.Response(
                200,
                content=b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there\n",
            )
        )

        result = await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

        assert result["transcript"][0]["text"] == "Hello there"

    @respx.mock
    async def test_ignores_non_transcript_files(self):
        mock_video("interview.mp4")
        mock_folder(
            [
                {"id": "img", "name": "thumbnail.png"},
                {"id": "doc", "name": "brief.pdf"},
            ]
        )

        with pytest.raises(ValueError, match="No transcript file found"):
            await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)


class TestActionableErrors:
    @respx.mock
    async def test_missing_transcript_explains_the_editor_workflow(self):
        """The fix is a manual export, so the error has to spell it out."""
        mock_video("interview.mp4")
        mock_folder([])

        with pytest.raises(ValueError, match="Export Transcript"):
            await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

    @respx.mock
    async def test_video_without_a_parent_folder_is_reported(self):
        respx.get(f"{FILES}/{VIDEO_ID}").mock(
            return_value=httpx.Response(200, json={"data": {"name": "orphan.mp4"}})
        )

        with pytest.raises(ValueError, match="no parent_id"):
            await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)

    @respx.mock
    async def test_transcript_without_a_download_url_names_the_file(self):
        mock_video("interview.mp4")
        mock_folder([{"id": "match", "name": "interview.srt"}])
        respx.get(f"{FILES}/match").mock(
            return_value=httpx.Response(
                200, json={"data": {"name": "interview.srt"}}
            )
        )

        with pytest.raises(ValueError, match="no download URL"):
            await get_transcript_from_sibling(TOKEN, ACCOUNT, VIDEO_ID)
