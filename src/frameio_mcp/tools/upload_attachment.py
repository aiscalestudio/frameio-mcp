"""Attach a local file to a Frame.io comment. Two-step: create record, then PUT bytes."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ..client import FrameIOClient
from ..config import Config


def _find_upload_url(attachment: dict) -> str | None:
    """Locate the presigned upload URL in the attachment creation response."""
    for key in ("upload_url", "upload"):
        v = attachment.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            nested = v.get("url") or v.get("upload_url")
            if isinstance(nested, str) and nested:
                return nested
    return None


async def upload_attachment(
    account_id: str,
    comment_id: str,
    file_path: str,
) -> dict:
    """Read a local file, create the attachment record, PUT the bytes to Frame.io's URL."""
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    file_bytes = path.read_bytes()
    file_size = len(file_bytes)
    file_name = path.name
    media_type, _ = mimetypes.guess_type(str(path))
    if not media_type:
        media_type = "application/octet-stream"

    config = Config.from_env()
    async with FrameIOClient(config) as client:
        attachment = await client.create_comment_attachment(
            account_id=account_id,
            comment_id=comment_id,
            file_name=file_name,
            media_type=media_type,
            file_size=file_size,
        )
        upload_url = _find_upload_url(attachment)
        if not upload_url:
            raise ValueError(
                f"Frame.io did not return an upload URL. Response: {attachment}. "
                f"The attachment endpoint may have a different response shape — "
                f"check Frame.io v4 OpenAPI spec at https://api.frame.io/v4/openapi.json"
            )
        await FrameIOClient.upload_to_presigned_url(upload_url, file_bytes, media_type)

    return {
        "attachment_id": attachment.get("id"),
        "comment_id": comment_id,
        "file_name": file_name,
        "media_type": media_type,
        "file_size_bytes": file_size,
        "url": attachment.get("url") or attachment.get("view_url"),
    }
