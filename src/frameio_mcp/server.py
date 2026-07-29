"""FastMCP server exposing the 5 Frame.io tools to any MCP-compatible client."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools.get_asset_from_url import get_asset_from_url as _get_asset_from_url
from .tools.get_transcript_from_sibling import (
    get_transcript_from_sibling as _get_transcript_from_sibling,
)
from .tools.list_comments import list_comments as _list_comments
from .tools.post_comment import post_comment as _post_comment
from .tools.upload_attachment import upload_attachment as _upload_attachment

mcp = FastMCP("frameio")


@mcp.tool()
async def frameio_get_asset_from_url(url: str) -> dict:
    """Resolve a Frame.io URL (project view, player, or review link) to identifiers.

    Returns account_id, workspace_id, project_id, file_id, file_name, media_type,
    and duration_seconds. Call this first whenever the user provides a Frame.io URL
    — every other tool needs account_id + file_id.

    Supports these URL patterns:
      https://next.frame.io/project/{project_id}/view/{file_id}
      https://next.frame.io/player/{file_id}
      https://app.frame.io/reviews/{review_id}/{file_id}
    """
    return await _get_asset_from_url(url)


@mcp.tool()
async def frameio_get_transcript_from_sibling(account_id: str, file_id: str) -> dict:
    """Find the SRT or VTT transcript file that lives in the same folder as a video, and parse it.

    Frame.io hasn't shipped a native transcript API yet, so this tool relies on the editor
    having exported the transcript (Frame.io UI → three-dot menu on the video → Export
    Transcript → SRT) and uploaded that file to the SAME folder as the video.

    Returns a structured transcript: a list of {start_seconds, end_seconds, text, speaker},
    plus source_file_name and source_file_id. Speaker labels come through when Frame.io
    Speaker ID was enabled at export time.

    If no sibling SRT/VTT is found, returns an actionable error telling you what the editor
    needs to do.
    """
    return await _get_transcript_from_sibling(account_id, file_id)


@mcp.tool()
async def frameio_post_comment(
    account_id: str,
    file_id: str,
    text: str,
    timestamp_seconds: float,
    duration_seconds: float | None = None,
) -> dict:
    """Post a frame-accurate timestamped comment on a Frame.io file.

    - timestamp_seconds is a float — fractional values allowed for sub-frame accuracy
    - duration_seconds is optional; supply it for range comments (comment spans a segment)
    - text supports Markdown

    Returns the new comment's id, text, resolved timestamp (in both microseconds and seconds),
    created_at, and a Frame.io URL.
    """
    return await _post_comment(account_id, file_id, text, timestamp_seconds, duration_seconds)


@mcp.tool()
async def frameio_list_comments(
    account_id: str,
    file_id: str,
    page_size: int = 50,
    after: str | None = None,
    only_mine: bool = False,
) -> dict:
    """List comments on a Frame.io file. Cursor-paginated.

    - page_size: 1 to 100, default 50
    - after: cursor from a previous call's next_cursor (for pagination)
    - only_mine: filter to comments the authenticated user posted. Useful when Fable 5
      needs to walk its own comment list back and act on each one.

    Returns comments (with timestamp_seconds already converted from microseconds),
    next_cursor for pagination, and total_count.
    """
    return await _list_comments(account_id, file_id, page_size, after, only_mine)


@mcp.tool()
async def frameio_upload_attachment(
    account_id: str,
    comment_id: str,
    file_path: str,
) -> dict:
    """Attach a local file (MP4, PNG, PDF, etc.) to a specific Frame.io comment.

    - file_path must be an absolute path on the machine running this MCP server
    - Media type is inferred from the file extension

    Returns the attachment_id, file_name, media_type, file_size_bytes, and Frame.io URL
    where the attached file becomes visible on the comment.
    """
    return await _upload_attachment(account_id, comment_id, file_path)
