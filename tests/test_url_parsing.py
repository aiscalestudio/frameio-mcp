"""Tests for Frame.io URL pattern parsing."""

from __future__ import annotations

from frameio_mcp.tools.get_asset_from_url import parse_frameio_url


def test_project_view_url():
    result = parse_frameio_url(
        "https://next.frame.io/project/061b75c8-0a31-467f-9c41-cffc901b143c"
        "/view/d12feb8b-640c-40d2-bb31-06d06f63a13a"
    )
    assert result["project_id"] == "061b75c8-0a31-467f-9c41-cffc901b143c"
    assert result["file_id"] == "d12feb8b-640c-40d2-bb31-06d06f63a13a"


def test_project_only_url():
    result = parse_frameio_url(
        "https://next.frame.io/project/061b75c8-0a31-467f-9c41-cffc901b143c"
    )
    assert result["project_id"] == "061b75c8-0a31-467f-9c41-cffc901b143c"
    assert "file_id" not in result


def test_player_url():
    result = parse_frameio_url(
        "https://next.frame.io/player/d12feb8b-640c-40d2-bb31-06d06f63a13a"
    )
    assert result["file_id"] == "d12feb8b-640c-40d2-bb31-06d06f63a13a"


def test_legacy_reviews_url():
    result = parse_frameio_url(
        "https://app.frame.io/reviews/review-id-abc/file-id-def"
    )
    assert result["review_id"] == "review-id-abc"
    assert result["file_id"] == "file-id-def"


def test_assets_url():
    result = parse_frameio_url(
        "https://app.frame.io/assets/file-id-xyz"
    )
    assert result["file_id"] == "file-id-xyz"


def test_unrecognized_url_returns_empty():
    result = parse_frameio_url("https://example.com/foo/bar")
    assert result == {}


def test_url_with_trailing_slash():
    result = parse_frameio_url(
        "https://next.frame.io/project/pid/view/fid/"
    )
    assert result["project_id"] == "pid"
    assert result["file_id"] == "fid"


def test_url_with_query_string_ignored():
    result = parse_frameio_url(
        "https://next.frame.io/project/pid/view/fid?utm_source=slack"
    )
    assert result["file_id"] == "fid"
