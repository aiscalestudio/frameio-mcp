"""Find + parse the SRT/VTT transcript file sibling of a video in Frame.io."""

from __future__ import annotations

import re
from io import StringIO
from pathlib import PurePosixPath

import srt
import webvtt

from ..client import FrameIOClient
from ..config import Config


# Speaker labels in Frame.io Speaker ID exports look like "[Speaker 1]: text"
_SPEAKER_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*(.*)$", re.DOTALL)


def _split_speaker(text: str) -> tuple[str | None, str]:
    m = _SPEAKER_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None, text


def _vtt_time_to_seconds(t: str) -> float:
    """Parse a VTT timestamp like '00:01:23.456' or '23:45.678' into float seconds."""
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        raise ValueError(f"Unrecognized VTT timestamp: {t}")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_srt(content: str) -> list[dict]:
    lines: list[dict] = []
    for sub in srt.parse(content):
        speaker, text = _split_speaker(sub.content.strip())
        lines.append({
            "start_seconds": sub.start.total_seconds(),
            "end_seconds": sub.end.total_seconds(),
            "text": text.strip(),
            "speaker": speaker,
        })
    return lines


def _parse_vtt(content: str) -> list[dict]:
    lines: list[dict] = []
    buffer = StringIO(content)
    for caption in webvtt.read_buffer(buffer):
        speaker, text = _split_speaker(caption.text.strip())
        lines.append({
            "start_seconds": _vtt_time_to_seconds(caption.start),
            "end_seconds": _vtt_time_to_seconds(caption.end),
            "text": text.strip(),
            "speaker": speaker,
        })
    return lines


def _extract_download_url(file_data: dict) -> str | None:
    """Locate the download URL in a Frame.io file object. Response shape varies slightly."""
    for key in ("download_url", "url", "original_url"):
        v = file_data.get(key)
        if isinstance(v, str) and v:
            return v
    downloads = file_data.get("downloads") or {}
    if isinstance(downloads, dict):
        for key in ("original", "source"):
            v = downloads.get(key)
            if isinstance(v, str) and v:
                return v
    # media_links.original is common in v4 responses
    media_links = file_data.get("media_links") or {}
    if isinstance(media_links, dict):
        for key in ("original", "high_quality"):
            block = media_links.get(key) or {}
            if isinstance(block, dict):
                v = block.get("download_url") or block.get("url")
                if isinstance(v, str) and v:
                    return v
    return None


async def get_transcript_from_sibling(account_id: str, file_id: str) -> dict:
    """Find the SRT/VTT transcript in the same folder as the video, download and parse it."""
    config = Config.from_env()

    async with FrameIOClient(config) as client:
        # 1. Get the video's parent folder
        video = await client.get_file(account_id, file_id)
        parent_id = video.get("parent_id")
        if not parent_id:
            raise ValueError(
                f"Video file {file_id} has no parent_id — cannot locate sibling transcript."
            )
        video_name = video.get("name") or ""
        video_base = PurePosixPath(video_name).stem.lower()

        # 2. List folder children, paginating if needed
        candidates: list[dict] = []
        after: str | None = None
        while True:
            result = await client.list_folder_children(
                account_id, parent_id, page_size=100, after=after
            )
            for child in result.get("data", []):
                name = (child.get("name") or "").lower()
                if name.endswith(".srt") or name.endswith(".vtt"):
                    candidates.append(child)
            after = (result.get("links") or {}).get("next")
            if not after:
                break

        if not candidates:
            raise ValueError(
                "No transcript file found in the video's folder. Editor should export "
                "the transcript from Frame.io UI (open the video → three-dot menu → "
                "Export Transcript → SRT) and upload the SRT file to the SAME folder "
                "as the video, then re-run."
            )

        # 3. Prefer exact base-name match; fall back to the single candidate; else error
        transcript_file: dict | None = None
        for candidate in candidates:
            candidate_base = PurePosixPath(candidate.get("name") or "").stem.lower()
            if candidate_base == video_base:
                transcript_file = candidate
                break
        if transcript_file is None:
            if len(candidates) == 1:
                transcript_file = candidates[0]
            else:
                names = [c.get("name") for c in candidates]
                raise ValueError(
                    f"Multiple transcript files in folder: {names}. None match the "
                    f"video's base name '{video_base}'. Rename the correct one to "
                    f"'{video_base}.srt' or remove the extras."
                )

        # 4. Fetch the full transcript file record so we get the download URL
        transcript_file_id = transcript_file["id"]
        transcript_full = await client.get_file(account_id, transcript_file_id)
        download_url = _extract_download_url(transcript_full)
        if not download_url:
            raise ValueError(
                f"Transcript file '{transcript_file.get('name')}' has no download URL "
                f"in its Frame.io metadata. Response keys: {list(transcript_full.keys())}"
            )

        # 5. Download the bytes and decode
        content_bytes = await client.download_file_content(download_url)
        content = content_bytes.decode("utf-8", errors="replace")

    # 6. Parse based on extension
    name = (transcript_file.get("name") or "").lower()
    if name.endswith(".srt"):
        transcript = _parse_srt(content)
    else:
        transcript = _parse_vtt(content)

    return {
        "transcript": transcript,
        "source_file_name": transcript_file.get("name"),
        "source_file_id": transcript_file_id,
    }
