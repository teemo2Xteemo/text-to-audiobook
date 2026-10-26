from app.application.pipeline.parse_srt import (
    format_srt_timecode,
    parse_srt_cues,
    serialize_srt_cues,
)
from app.domain.srt import SRTCue


def test_parse_srt_cues_valid():
    raw = (
        "1\n00:00:01,500 --> 00:00:04,200\nHello world!\n\n"
        "2\n00:00:05.100 --> 00:00:07.800\nSecond line <i>styled</i>.\n"
    )
    cues = parse_srt_cues(raw)
    assert len(cues) == 2
    assert cues[0] == SRTCue(index=1, start_ms=1500, end_ms=4200, text="Hello world!")
    assert cues[1] == SRTCue(index=2, start_ms=5100, end_ms=7800, text="Second line styled.")
    assert format_srt_timecode(1500) == "00:00:01,500"


def test_parse_srt_cues_empty_and_whitespace():
    assert parse_srt_cues("") == []
    assert parse_srt_cues("   \n\n\t  ") == []


def test_parse_srt_cues_multiline_text():
    raw = "1\n00:00:00,000 --> 00:00:02,000\nLine one\nLine two\n\n"
    cues = parse_srt_cues(raw)
    assert len(cues) == 1
    assert cues[0] == SRTCue(index=1, start_ms=0, end_ms=2000, text="Line one Line two")


def test_parse_srt_cues_crlf_and_bom():
    raw = "\ufeff1\r\n00:00:01,000 --> 00:00:03,000\r\nWindows newlines\r\n\r\n"
    cues = parse_srt_cues(raw)
    assert len(cues) == 1
    assert cues[0] == SRTCue(index=1, start_ms=1000, end_ms=3000, text="Windows newlines")


def test_parse_srt_cues_webvtt_header_and_auto_index():
    raw = (
        "WEBVTT\n\n"
        "00:00:01.250 --> 00:00:03.500 position:50%\nFirst without index\n\n"
        "00:00:04.000 --> 00:00:06.000\nSecond without index\n"
    )
    cues = parse_srt_cues(raw)
    assert len(cues) == 2
    assert cues[0] == SRTCue(index=1, start_ms=1250, end_ms=3500, text="First without index")
    assert cues[1] == SRTCue(index=2, start_ms=4000, end_ms=6000, text="Second without index")


def test_format_srt_timecode():
    assert format_srt_timecode(0) == "00:00:00,000"
    assert format_srt_timecode(1500) == "00:00:01,500"
    assert format_srt_timecode(3661234) == "01:01:01,234"
    assert format_srt_timecode(-50) == "00:00:00,000"


def test_srt_cue_duration_ms():
    cue = SRTCue(index=1, start_ms=1200, end_ms=4500, text="test")
    assert cue.duration_ms == 3300


def test_serialize_srt_cues_roundtrip():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=3000, text="Hello world!"),
        SRTCue(index=2, start_ms=5000, end_ms=7500, text="Second line."),
    ]
    serialized = serialize_srt_cues(cues)
    assert serialized == (
        "1\n00:00:01,000 --> 00:00:03,000\nHello world!\n\n"
        "2\n00:00:05,000 --> 00:00:07,500\nSecond line.\n"
    )
    parsed = parse_srt_cues(serialized)
    assert parsed == cues


def test_serialize_srt_cues_empty():
    assert serialize_srt_cues([]) == ""
