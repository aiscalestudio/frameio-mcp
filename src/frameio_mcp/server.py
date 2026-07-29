"""MCP server exposing the five Frame.io tools.

Auth model: every tool acts as the calling user. Claude authenticates the user against
Adobe IMS through FastMCP's OIDCProxy, and `require_frameio_token()` pulls that user's
Adobe access token out of the request context. The server stores no Frame.io
credentials of its own and has no notion of a "default" user.

Token lifecycle is handled upstream by OAuthProxy, including refresh under a lock, so
nothing here writes a token to disk.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from .tools.get_asset_from_url import get_asset_from_url as _get_asset_from_url
from .tools.get_transcript_from_sibling import (
    get_transcript_from_sibling as _get_transcript_from_sibling,
)
from .tools.list_comments import list_comments as _list_comments
from .tools.post_comment import post_comment as _post_comment
from .tools.upload_attachment import upload_attachment as _upload_attachment

mcp = FastMCP("frameio")


def require_frameio_token() -> str:
    """Return the calling user's Adobe access token, or explain why there isn't one.

    Scope adequacy is enforced upstream by AdobeIMSTokenVerifier, which denies a token
    missing any of REQUIRED_V4_SCOPES and logs which one. That check exists because a
    token short one scope is indistinguishable from a good one here, and Frame.io reports
    it only as a bare 401.
    """
    token = get_access_token()
    if token is None or not token.token:
        raise ValueError(
            "No Frame.io credentials for this request. Reconnect the Frame.io "
            "connector and sign in with your Adobe ID."
        )
    return token.token


@mcp.tool
async def frameio_get_asset_from_url(url: str) -> dict:
    """Resolve a Frame.io URL (project view, player, or review link) to identifiers.

    Returns account_id, workspace_id, project_id, file_id, file_name, media_type,
    duration_seconds, and frame_rate. Call this first whenever the user provides a
    Frame.io URL — every other tool needs account_id + file_id, and duration_seconds is
    the only reliable way to know whether a requested timestamp exists in the video.

    Supports these URL patterns:
      https://next.frame.io/project/{project_id}/view/{file_id}
      https://next.frame.io/player/{file_id}
      https://app.frame.io/reviews/{review_id}/{file_id}
    """
    return await _get_asset_from_url(require_frameio_token(), url)


@mcp.tool
async def frameio_get_transcript_from_sibling(account_id: str, file_id: str) -> dict:
    """Find the SRT or VTT transcript file in the same folder as a video, and parse it.

    Frame.io hasn't shipped a native transcript API yet, so this tool relies on the editor
    having exported the transcript (Frame.io UI → three-dot menu on the video → Export
    Transcript → SRT) and uploaded that file to the SAME folder as the video.

    Returns a structured transcript: a list of {start_seconds, end_seconds, text, speaker},
    plus source_file_name and source_file_id. Speaker labels come through when Frame.io
    Speaker ID was enabled at export time.

    If no sibling SRT/VTT is found, returns an actionable error telling you what the editor
    needs to do.
    """
    return await _get_transcript_from_sibling(
        require_frameio_token(), account_id, file_id
    )


@mcp.tool
async def frameio_post_comment(
    account_id: str,
    file_id: str,
    text: str,
    timestamp_seconds: float,
    duration_seconds: float | None = None,
) -> dict:
    """Post a frame-accurate timestamped comment on a Frame.io file.

    `timestamp_seconds` is a position in ordinary playback seconds, exactly as a person
    would read it off the player. 90.0 means one minute thirty. Do NOT scale, convert, or
    compensate for units: this tool reads the file's frame rate and does the conversion
    itself. Passing a fraction of a second to "compensate" puts the comment at the very
    start of the video.

    Call frameio_get_asset_from_url first if you do not already know the video's length.
    It reports duration_seconds, so you can tell whether the position you want exists.
    Do not infer the duration from other comments' timestamps.

    - duration_seconds is optional; supply it for a range comment spanning a segment
    - text supports Markdown
    - the comment is attributed to the signed-in user, not a shared service account

    Returns the comment's id, text, the frame it landed on, the equivalent in seconds,
    the file's frame_rate, created_at, and a Frame.io URL. If the requested position is
    past the end of the video, the error names the actual duration.
    """
    return await _post_comment(
        require_frameio_token(),
        account_id,
        file_id,
        text,
        timestamp_seconds,
        duration_seconds,
    )


@mcp.tool
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
    - only_mine: filter to comments the authenticated user posted

    Returns comments (with timestamp_seconds already converted from microseconds),
    next_cursor for pagination, and total_count.
    """
    return await _list_comments(
        require_frameio_token(), account_id, file_id, page_size, after, only_mine
    )


@mcp.tool
async def frameio_upload_attachment(
    account_id: str,
    comment_id: str,
    file_name: str,
    source_url: str | None = None,
    content_base64: str | None = None,
) -> dict:
    """Attach a file (MP4, PNG, PDF, etc.) to a specific Frame.io comment.

    Supply exactly one source:
    - source_url: a publicly reachable http(s) URL the server fetches. Private,
      loopback, and link-local addresses are refused.
    - content_base64: the file contents inline, base64-encoded.

    file_name determines the media type and the name shown in Frame.io.
    Maximum size is 25 MB.

    Returns the attachment_id, file_name, media_type, file_size_bytes, and Frame.io URL
    where the attached file becomes visible on the comment.
    """
    return await _upload_attachment(
        require_frameio_token(),
        account_id,
        comment_id,
        file_name,
        source_url,
        content_base64,
    )
