import pytest

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


def test_parse_srt_cues_strips_ass_override_tags():
    raw = "1\n00:00:00,000 --> 00:00:02,000\n{\\an8}Top subtitle {\\i1}italic{\\i0}\n\n"
    cues = parse_srt_cues(raw)
    assert len(cues) == 1
    assert cues[0].text == "Top subtitle italic"


def test_serialize_srt_cues_collapses_blank_lines_within_cue():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=3000, text="Line A\n\nLine B\n   \nLine C"),
        SRTCue(index=2, start_ms=4000, end_ms=6000, text="Second cue"),
    ]
    serialized = serialize_srt_cues(cues)
    parsed = parse_srt_cues(serialized)
    assert len(parsed) == 2
    assert parsed[0].text == "Line A Line B Line C"
    assert parsed[1].text == "Second cue"


def test_parse_srt_cues_drops_sound_effects_and_merges_rolling_captions() -> None:
    from app.application.pipeline.parse_srt import parse_srt_cues

    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n[Music]\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nHello there (applause)\n\n"
        "3\n00:00:04,000 --> 00:00:05,000\nHello there my friend\n\n"
        "4\n00:00:09,000 --> 00:00:10,000\n\u266a la la \u266a\n\n"
        "5\n00:00:11,000 --> 00:00:12,000\nGoodbye.\n"
    )
    cues = parse_srt_cues(srt)

    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
        (3000, 5000, "Hello there my friend"),
        (11000, 12000, "Goodbye."),
    ]


def test_parse_srt_cues_keeps_distant_repeats_and_long_parentheticals() -> None:
    from app.application.pipeline.parse_srt import parse_srt_cues

    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\nNo.\n\n"
        "2\n00:00:40,000 --> 00:00:41,000\nNo.\n\n"
        "3\n00:00:41,200 --> 00:00:42,000\nNo way.\n\n"
        "4\n00:00:43,000 --> 00:00:45,000\n(Anh ấy nghĩ rằng cô ấy sẽ quay lại)\n\n"
        "5\n00:00:46,000 --> 00:00:47,000\n(sighs)\n\n"
        "6\n00:00:48,000 --> 00:00:49,000\n*đó là lời nói dối lớn nhất*\n"
    )
    cues = parse_srt_cues(srt)

    assert [c.text for c in cues] == [
        "No.",
        "No.",
        "No way.",
        "(Anh ấy nghĩ rằng cô ấy sẽ quay lại)",
        "*đó là lời nói dối lớn nhất*",
    ]
    assert cues[0].end_ms == 2000  # the distant repeat did not stretch the first cue


@pytest.mark.parametrize(
    ("line", "is_effect"),
    [
        ("[Music]", True),
        ("\u266a la la \u266a", True),
        ("(sighs)", True),
        ("(Sighs)", True),
        ("(door slams)", True),
        ("*APPLAUSE*", True),
        ("(\u7b11)", True),
        ("\uff08\u7b11\uff09", True),
        ("(\u4ed6\u5fc3\u60f3\u8fd9\u7edd\u5bf9\u4e0d\u53ef\u80fd\u662f\u771f\u7684)", False),
        ("(Thật sao?)", False),
        ("(Hắn nghĩ vậy)", False),
        ("*Chạy mau!*", False),
        ("(Anh ấy nghĩ rằng cô ấy sẽ quay lại)", False),
        ("Hello there.", False),
    ],
)
def test_is_sound_effect_line_separates_cues_from_asides(line: str, is_effect: bool) -> None:
    from app.application.pipeline.parse_srt import is_sound_effect_line

    assert is_sound_effect_line(line) is is_effect
