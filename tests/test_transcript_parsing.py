"""Tests for SRT/VTT parsing and speaker label extraction."""

from __future__ import annotations

import pytest

from frameio_mcp.tools.get_transcript_from_sibling import (
    _parse_srt,
    _parse_vtt,
    _split_speaker,
    _vtt_time_to_seconds,
)


class TestSpeakerSplit:
    def test_extracts_speaker_label(self):
        speaker, text = _split_speaker("[Speaker 1]: Hello world")
        assert speaker == "Speaker 1"
        assert text == "Hello world"

    def test_no_speaker_label_preserves_text(self):
        speaker, text = _split_speaker("Just regular caption text")
        assert speaker is None
        assert text == "Just regular caption text"

    def test_speaker_with_custom_name(self):
        speaker, text = _split_speaker("[Laura]: Testing 123")
        assert speaker == "Laura"
        assert text == "Testing 123"

    def test_multiline_text_preserved(self):
        speaker, text = _split_speaker("[Speaker 2]: Line one\nLine two")
        assert speaker == "Speaker 2"
        assert text == "Line one\nLine two"


class TestVttTimeConversion:
    def test_hms_ms(self):
        assert _vtt_time_to_seconds("01:23:45.678") == pytest.approx(5025.678)

    def test_ms_only(self):
        assert _vtt_time_to_seconds("23:45.678") == pytest.approx(1425.678)

    def test_zero_time(self):
        assert _vtt_time_to_seconds("00:00:00.000") == 0.0

    def test_fractional_seconds(self):
        assert _vtt_time_to_seconds("00:00:01.500") == pytest.approx(1.5)


class TestSrtParsing:
    def test_basic_srt(self):
        srt_content = """1
00:00:01,000 --> 00:00:03,500
Hello world

2
00:00:04,000 --> 00:00:06,000
Second line
"""
        result = _parse_srt(srt_content)
        assert len(result) == 2
        assert result[0]["start_seconds"] == 1.0
        assert result[0]["end_seconds"] == 3.5
        assert result[0]["text"] == "Hello world"
        assert result[0]["speaker"] is None

    def test_srt_with_speaker_labels(self):
        srt_content = """1
00:00:01,000 --> 00:00:03,000
[Speaker 1]: Welcome to the show

2
00:00:04,000 --> 00:00:06,000
[Speaker 2]: Great to be here
"""
        result = _parse_srt(srt_content)
        assert len(result) == 2
        assert result[0]["speaker"] == "Speaker 1"
        assert result[0]["text"] == "Welcome to the show"
        assert result[1]["speaker"] == "Speaker 2"


class TestVttParsing:
    def test_basic_vtt(self):
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:03.500
Hello world

00:00:04.000 --> 00:00:06.000
Second line
"""
        result = _parse_vtt(vtt_content)
        assert len(result) == 2
        assert result[0]["start_seconds"] == 1.0
        assert result[0]["end_seconds"] == 3.5
        assert result[0]["text"] == "Hello world"

    def test_vtt_with_speaker_labels(self):
        vtt_content = """WEBVTT

00:00:01.000 --> 00:00:03.000
[Speaker 1]: Welcome to the show

00:00:04.000 --> 00:00:06.000
[Speaker 2]: Great to be here
"""
        result = _parse_vtt(vtt_content)
        assert len(result) == 2
        assert result[0]["speaker"] == "Speaker 1"
        assert result[0]["text"] == "Welcome to the show"
        assert result[1]["speaker"] == "Speaker 2"
