import pytest

from app.application.pipeline.subtitle_formats import (
    detect_document_format,
    parse_document,
    render_document,
    write_timed_document,
)
from app.domain.subtitles import DocumentFormat

SRT_SAMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "Hello there.\n"
    "\n"
    "2\n"
    "00:00:02,000 --> 00:00:03,500\n"
    "Second line\n"
)

VTT_SAMPLE = (
    "WEBVTT\n"
    "Kind: captions\n"
    "\n"
    "NOTE\n"
    "Keep this note\n"
    "\n"
    "intro\n"
    "00:00:01.000 --> 00:00:02.000 align:start\n"
    "Hello there\n"
    "\n"
    "00:00:03.000 --> 00:00:04.000\n"
    "Second line\n"
)

ASS_SAMPLE = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,director note\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,Bob,0,0,0,,{\\an8}Hi, you\\Nthere\n"
)


@pytest.mark.parametrize("fixture", [SRT_SAMPLE, VTT_SAMPLE, ASS_SAMPLE])
def test_round_trip_with_raw_text_is_identity(fixture: str) -> None:
    doc = parse_document(fixture)
    assert doc is not None
    assert render_document(doc, [cue.raw for cue in doc.cues]) == fixture


def test_detect_srt_with_bom_and_crlf() -> None:
    assert (
        detect_document_format("\ufeff1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n")
        is DocumentFormat.SRT
    )


def test_detect_formats() -> None:
    assert detect_document_format(VTT_SAMPLE) is DocumentFormat.VTT
    assert detect_document_format(ASS_SAMPLE) is DocumentFormat.ASS
    assert detect_document_format("Just a story.\n\nSecond paragraph.") is DocumentFormat.TXT


def test_txt_extension_with_srt_content_is_srt() -> None:
    assert detect_document_format(SRT_SAMPLE) is DocumentFormat.SRT


def test_vtt_content_in_srt_name_is_still_vtt() -> None:
    assert detect_document_format(VTT_SAMPLE) is DocumentFormat.VTT


def test_srt_slots_keep_timing_and_clean_text() -> None:
    doc = parse_document("1\n00:00:01,000 --> 00:00:02,500\n{\\an8}<i>Hello</i>\nthere\n")
    assert doc is not None
    (cue,) = doc.cues
    assert (cue.start_ms, cue.end_ms, cue.text, cue.prefix) == (
        1000,
        2500,
        "Hello there",
        "{\\an8}",
    )


def test_multiline_cue_collapses_to_one_translated_line() -> None:
    source = "1\n00:00:01,000 --> 00:00:02,500\n{\\an8}<i>Hello</i>\nthere\n"
    doc = parse_document(source)
    assert doc is not None
    rendered = render_document(doc, ["Xin chào"])
    assert rendered == "1\n00:00:01,000 --> 00:00:02,500\n{\\an8}Xin chào\n"


def test_sound_effect_cue_rendered_verbatim() -> None:
    doc = parse_document("1\n00:00:01,000 --> 00:00:02,000\n[Music]\n")
    assert doc is not None
    assert not doc.cues[0].translatable
    assert render_document(doc, ["IGNORED"]) == "1\n00:00:01,000 --> 00:00:02,000\n[Music]\n"


def test_music_note_cue_is_not_translatable() -> None:
    doc = parse_document("1\n00:00:01,000 --> 00:00:02,000\n♪ theme ♪\n")
    assert doc is not None
    assert not doc.cues[0].translatable
    assert "♪ theme ♪" in render_document(doc, ["IGNORED"])


def test_empty_cue_is_kept() -> None:
    source = "1\n00:00:01,000 --> 00:00:02,000\n\n\n2\n00:00:03,000 --> 00:00:04,000\nHi\n"
    doc = parse_document(source)
    assert doc is not None
    assert len(doc.cues) == 2
    assert doc.cues[0].translatable is False
    assert doc.cues[1].text == "Hi"
    assert render_document(doc, [cue.raw for cue in doc.cues]) == source


def test_overlapping_cues_keep_their_times() -> None:
    source = "1\n00:00:01,000 --> 00:00:03,000\nOne.\n\n2\n00:00:02,000 --> 00:00:04,000\nTwo.\n"
    doc = parse_document(source)
    assert doc is not None
    assert [(cue.start_ms, cue.end_ms) for cue in doc.cues] == [(1000, 3000), (2000, 4000)]
    rendered = render_document(doc, ["Một.", "Hai."])
    again = parse_document(rendered)
    assert again is not None
    assert [(cue.start_ms, cue.end_ms) for cue in again.cues] == [(1000, 3000), (2000, 4000)]


def test_trailing_spaces_on_timecode_still_parse() -> None:
    source = "1\n00:00:01,000 --> 00:00:02,000   \nHi\n"
    doc = parse_document(source)
    assert doc is not None
    assert doc.cues[0].start_ms == 1000
    assert doc.cues[0].end_ms == 2000
    assert render_document(doc, [doc.cues[0].raw]) == source


def test_ass_only_text_field_changes() -> None:
    doc = parse_document(ASS_SAMPLE)
    assert doc is not None
    out = render_document(doc, ["Xin chào"] * len(doc.cues))
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,Bob,0,0,0,,{\\an8}Xin chào" in out
    assert "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,director note" in out
    assert out.split("[Events]")[0] == ASS_SAMPLE.split("[Events]")[0]


def test_vtt_keeps_header_notes_and_cue_settings() -> None:
    doc = parse_document(VTT_SAMPLE)
    assert doc is not None
    out = render_document(doc, ["X"] * len(doc.cues))
    assert out.startswith("WEBVTT")
    assert "NOTE" in out
    assert "align:start" in out
    assert "intro\n" in out


def test_write_timed_srt() -> None:
    assert (
        write_timed_document(DocumentFormat.SRT, [(0, 2000, "Xin chào")])
        == "1\n00:00:00,000 --> 00:00:02,000\nXin chào\n"
    )


def test_write_timed_vtt_and_ass() -> None:
    vtt = write_timed_document(DocumentFormat.VTT, [(0, 1500, "Xin chào")])
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.500\nXin chào\n" in vtt
    ass = write_timed_document(DocumentFormat.ASS, [(0, 2000, "Xin chào")])
    assert "Style: Default,Arial,64,&H00FFFFFF," in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Xin chào\n" in ass
    assert ",2,60,60,50,1" in ass


def test_many_cues_keep_count_and_order() -> None:
    blocks = [
        f"{index}\n00:00:{index % 60:02d},000 --> 00:00:{index % 60:02d},500\nLine {index}\n"
        for index in range(1, 301)
    ]
    source = "\n".join(blocks)
    doc = parse_document(source)
    assert doc is not None
    assert len(doc.cues) == 300
    assert doc.cues[0].text == "Line 1"
    assert doc.cues[-1].text == "Line 300"
    assert render_document(doc, [cue.raw for cue in doc.cues]) == source
