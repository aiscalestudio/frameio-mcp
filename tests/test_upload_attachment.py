"""Tests for upload_attachment after it stopped reading the local filesystem.

Two things changed and both need covering:

  1. The source is now a URL or a base64 payload. A local path was meaningless once
     the server stopped running on the user's machine.
  2. Fetching a caller-supplied URL from inside our own network is an SSRF sink. The
     server sits on infrastructure with access to metadata endpoints and internal
     services, so the fetcher has to refuse private destinations.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from frameio_mcp.config import FRAMEIO_API_BASE_URL
from frameio_mcp.tools.upload_attachment import (
    MAX_ATTACHMENT_BYTES,
    AttachmentSourceError,
    resolve_media_type,
    upload_attachment,
)

ATTACHMENT_URL = f"{FRAMEIO_API_BASE_URL}/accounts/acct-1/comments/cmt-1/attachments"
PRESIGNED = "https://frameio-uploads.example.com/presigned-put"


@pytest.fixture
def public_dns(monkeypatch):
    """Pretend test hostnames resolve to a public address.

    The SSRF guard does a real DNS lookup, which unit tests must not depend on.
    Tests that exercise the guard itself use literal IPs and bypass this.
    """
    import ipaddress

    monkeypatch.setattr(
        "frameio_mcp.tools.upload_attachment._resolve_addresses",
        lambda hostname: [ipaddress.ip_address("93.184.216.34")],
    )


def mock_frameio_accepts_attachment():
    respx.post(ATTACHMENT_URL).mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "id": "att-1",
                    "upload_url": PRESIGNED,
                    "url": "https://next.frame.io/attachments/att-1",
                }
            },
        )
    )
    return respx.put(PRESIGNED).mock(return_value=httpx.Response(200))


class TestMediaTypeResolution:
    @pytest.mark.parametrize(
        "file_name,expected",
        [
            ("clip.mp4", "video/mp4"),
            ("frame.png", "image/png"),
            ("notes.pdf", "application/pdf"),
        ],
    )
    def test_infers_from_extension(self, file_name, expected):
        assert resolve_media_type(file_name) == expected

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert resolve_media_type("mystery.zzz") == "application/octet-stream"


class TestSourceValidation:
    async def test_requires_a_source(self):
        with pytest.raises(AttachmentSourceError, match="exactly one"):
            await upload_attachment("tok", "acct-1", "cmt-1", "a.png")

    async def test_rejects_both_sources_at_once(self):
        """Ambiguity here would silently pick one and discard the caller's other input."""
        with pytest.raises(AttachmentSourceError, match="exactly one"):
            await upload_attachment(
                "tok",
                "acct-1",
                "cmt-1",
                "a.png",
                source_url="https://example.com/a.png",
                content_base64="AAAA",
            )

    async def test_rejects_malformed_base64(self):
        with pytest.raises(AttachmentSourceError, match="base64"):
            await upload_attachment(
                "tok", "acct-1", "cmt-1", "a.png", content_base64="not!valid!base64!"
            )

    async def test_rejects_empty_content(self):
        with pytest.raises(AttachmentSourceError, match="empty"):
            await upload_attachment(
                "tok", "acct-1", "cmt-1", "a.png", content_base64=""
            )


class TestSsrfProtection:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud instance metadata
            "http://localhost:8000/internal",
            "http://127.0.0.1/admin",
            "http://10.0.0.5/private",
            "http://192.168.1.1/router",
            "http://[::1]/loopback",
        ],
    )
    async def test_refuses_private_and_loopback_destinations(self, url):
        """A caller-supplied URL is fetched by the server, from inside its network."""
        with pytest.raises(AttachmentSourceError, match="not allowed"):
            await upload_attachment("tok", "acct-1", "cmt-1", "a.png", source_url=url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a.png"])
    async def test_refuses_non_http_schemes(self, url):
        with pytest.raises(AttachmentSourceError, match="http"):
            await upload_attachment("tok", "acct-1", "cmt-1", "a.png", source_url=url)


class TestSizeLimit:
    async def test_rejects_oversized_base64_payload(self):
        oversized = base64.b64encode(b"x" * (MAX_ATTACHMENT_BYTES + 1)).decode()
        with pytest.raises(AttachmentSourceError, match="too large"):
            await upload_attachment(
                "tok", "acct-1", "cmt-1", "big.bin", content_base64=oversized
            )

    @respx.mock
    async def test_rejects_oversized_url_by_content_length(self, public_dns):
        """Checked before downloading, so an oversized file is never pulled into memory."""
        respx.get("https://cdn.example.com/big.mp4").mock(
            return_value=httpx.Response(
                200,
                headers={"content-length": str(MAX_ATTACHMENT_BYTES + 1)},
                content=b"",
            )
        )
        with pytest.raises(AttachmentSourceError, match="too large"):
            await upload_attachment(
                "tok",
                "acct-1",
                "cmt-1",
                "big.mp4",
                source_url="https://cdn.example.com/big.mp4",
            )


class TestSuccessfulUpload:
    @respx.mock
    async def test_uploads_from_base64(self):
        put_route = mock_frameio_accepts_attachment()
        payload = b"\x89PNG fake image bytes"

        result = await upload_attachment(
            "tok",
            "acct-1",
            "cmt-1",
            "frame.png",
            content_base64=base64.b64encode(payload).decode(),
        )

        assert result["attachment_id"] == "att-1"
        assert result["media_type"] == "image/png"
        assert result["file_size_bytes"] == len(payload)
        assert put_route.calls.last.request.content == payload

    @respx.mock
    async def test_uploads_from_url(self, public_dns):
        payload = b"video bytes"
        respx.get("https://cdn.example.com/clip.mp4").mock(
            return_value=httpx.Response(200, content=payload)
        )
        put_route = mock_frameio_accepts_attachment()

        result = await upload_attachment(
            "tok",
            "acct-1",
            "cmt-1",
            "clip.mp4",
            source_url="https://cdn.example.com/clip.mp4",
        )

        assert result["file_size_bytes"] == len(payload)
        assert result["media_type"] == "video/mp4"
        assert put_route.calls.last.request.content == payload

    @respx.mock
    async def test_declared_size_matches_bytes_actually_sent(self):
        """Frame.io reserves the upload against this number; a mismatch fails the PUT."""
        create_route = respx.post(ATTACHMENT_URL).mock(
            return_value=httpx.Response(
                201, json={"data": {"id": "att-1", "upload_url": PRESIGNED}}
            )
        )
        respx.put(PRESIGNED).mock(return_value=httpx.Response(200))
        payload = b"exactly these bytes"

        await upload_attachment(
            "tok",
            "acct-1",
            "cmt-1",
            "a.bin",
            content_base64=base64.b64encode(payload).decode(),
        )

        import json as jsonlib

        body = jsonlib.loads(create_route.calls.last.request.content)
        assert body["data"]["file_size"] == len(payload)


class TestFrameioResponseHandling:
    @respx.mock
    async def test_missing_upload_url_is_reported_actionably(self):
        """The v4 attachment response shape was never confirmed against the live API."""
        respx.post(ATTACHMENT_URL).mock(
            return_value=httpx.Response(201, json={"data": {"id": "att-1"}})
        )

        with pytest.raises(ValueError, match="upload URL"):
            await upload_attachment(
                "tok", "acct-1", "cmt-1", "a.png", content_base64="QUFB"
            )
