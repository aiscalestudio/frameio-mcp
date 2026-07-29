"""Attach a file to a Frame.io comment. Two-step: create the record, then PUT bytes.

The source is a URL or a base64 payload, never a filesystem path. Once the server is
hosted, the caller's disk is not reachable from it, so a path argument would only ever
resolve against the server's own filesystem, which is both useless and a traversal risk.

Fetching a caller-supplied URL makes this an SSRF sink: the request originates from our
infrastructure, which can reach cloud metadata endpoints and internal services that the
caller cannot. `_assert_safe_url` is what stops that.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import mimetypes
import socket
from urllib.parse import urlparse

import httpx

from ..client import FrameIOClient

# Frame.io accepts larger files, but the whole payload is held in memory here and
# serverless functions have a hard memory ceiling. Raise this only alongside a
# streaming upload path.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

DOWNLOAD_TIMEOUT_SECONDS = 120.0


class AttachmentSourceError(ValueError):
    """Raised when the caller-supplied attachment source is unusable or unsafe."""


def resolve_media_type(file_name: str) -> str:
    """Infer the media type from the file name, defaulting to a safe generic."""
    media_type, _ = mimetypes.guess_type(file_name)
    return media_type or "application/octet-stream"


def _assert_safe_url(raw_url: str) -> None:
    """Refuse anything that would make the server fetch from its own network.

    Resolves the hostname and checks every address it maps to, because a public
    hostname can legitimately resolve to a private address.
    """
    parsed = urlparse(raw_url)

    if parsed.scheme not in ("http", "https"):
        raise AttachmentSourceError(
            f"source_url must be http or https, got {parsed.scheme!r}."
        )

    hostname = parsed.hostname
    if not hostname:
        raise AttachmentSourceError(f"source_url has no host: {raw_url!r}")

    for address in _resolve_addresses(hostname):
        if not address.is_global or address.is_loopback or address.is_link_local:
            raise AttachmentSourceError(
                f"source_url resolves to {address}, which is not allowed. Private, "
                f"loopback, and link-local addresses are refused because the server "
                f"fetches this URL from inside its own network."
            )


def _resolve_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every IP the hostname maps to. A literal IP resolves to itself."""
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise AttachmentSourceError(f"Could not resolve {hostname!r}: {e}") from e

    return [ipaddress.ip_address(info[4][0]) for info in infos]


def _decode_base64(content_base64: str) -> bytes:
    if not content_base64:
        raise AttachmentSourceError("content_base64 is empty.")
    try:
        payload = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise AttachmentSourceError(f"content_base64 is not valid base64: {e}") from e
    if not payload:
        raise AttachmentSourceError("content_base64 decoded to empty content.")
    return payload


async def _download(source_url: str) -> bytes:
    """Fetch the attachment bytes, refusing oversized responses before reading them."""
    _assert_safe_url(source_url)

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        response = await client.get(source_url, follow_redirects=False)

        if response.status_code >= 400:
            raise AttachmentSourceError(
                f"Could not fetch source_url ({response.status_code}): {source_url}"
            )

        declared = response.headers.get("content-length")
        if declared and int(declared) > MAX_ATTACHMENT_BYTES:
            raise AttachmentSourceError(
                f"Attachment is too large: {int(declared)} bytes exceeds the "
                f"{MAX_ATTACHMENT_BYTES} byte limit."
            )

        payload = response.content

    if not payload:
        raise AttachmentSourceError(f"source_url returned no content: {source_url}")

    _assert_within_size_limit(len(payload))
    return payload


def _assert_within_size_limit(size: int) -> None:
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentSourceError(
            f"Attachment is too large: {size} bytes exceeds the "
            f"{MAX_ATTACHMENT_BYTES} byte limit."
        )


async def _resolve_content(
    source_url: str | None, content_base64: str | None
) -> bytes:
    """Exactly one source must be supplied. Accepting both would silently drop one.

    Presence is tested against None rather than truthiness, so an empty string counts
    as "supplied but empty" and gets a precise error instead of "you supplied nothing".
    """
    if (source_url is None) == (content_base64 is None):
        raise AttachmentSourceError(
            "Supply exactly one of source_url or content_base64."
        )

    if source_url is not None:
        return await _download(source_url)

    payload = _decode_base64(content_base64 or "")
    _assert_within_size_limit(len(payload))
    return payload


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
    access_token: str,
    account_id: str,
    comment_id: str,
    file_name: str,
    source_url: str | None = None,
    content_base64: str | None = None,
) -> dict:
    """Attach a file to a Frame.io comment from a URL or an inline base64 payload."""
    file_bytes = await _resolve_content(source_url, content_base64)
    file_size = len(file_bytes)
    media_type = resolve_media_type(file_name)

    async with FrameIOClient(access_token) as client:
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
                f"check the Frame.io v4 OpenAPI spec at "
                f"https://api.frame.io/v4/openapi.json"
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
