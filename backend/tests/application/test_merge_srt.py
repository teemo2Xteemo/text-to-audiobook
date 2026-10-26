import pytest

from app.application.pipeline.merge_srt import merge_srt_cues
from app.domain.srt import SRTCue, TimedChunk


def test_merge_srt_cues_joins_incomplete_sentences():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=2500, text="In this tutorial"),
        SRTCue(index=2, start_ms=2600, end_ms=4500, text="we will learn Python."),
        SRTCue(index=3, start_ms=6000, end_ms=8000, text="Let's get started!"),
    ]
    chunks = merge_srt_cues(cues)
    assert len(chunks) == 2
    assert chunks[0] == TimedChunk(
        chunk_index=0,
        start_ms=1000,
        end_ms=4500,
        text="In this tutorial we will learn Python.",
        cue_indices=[1, 2],
    )
    assert chunks[1] == TimedChunk(
        chunk_index=1,
        start_ms=6000,
        end_ms=8000,
        text="Let's get started!",
        cue_indices=[3],
    )


def test_merge_srt_cues_empty_and_whitespace():
    assert merge_srt_cues([]) == []
    cues = [
        SRTCue(index=1, start_ms=0, end_ms=1000, text=""),
        SRTCue(index=2, start_ms=1100, end_ms=2000, text="   \t\n  "),
    ]
    assert merge_srt_cues(cues) == []


def test_merge_srt_cues_splits_on_pause_exceeding_threshold():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=2000, text="Part one without punctuation"),
        SRTCue(index=2, start_ms=3000, end_ms=4000, text="part two after pause"),
    ]
    # Pause between 2000 and 3000 is 1000ms > max_pause_ms (400ms)
    chunks = merge_srt_cues(cues, max_pause_ms=400)
    assert len(chunks) == 2
    assert chunks[0].text == "Part one without punctuation"
    assert chunks[0].cue_indices == [1]
    assert chunks[1].text == "part two after pause"
    assert chunks[1].cue_indices == [2]


def test_merge_srt_cues_splits_on_duration_exceeding_threshold():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=12000, text="Sentence fragment one"),
        SRTCue(index=2, start_ms=12100, end_ms=22000, text="sentence fragment two."),
    ]
    # Total duration from 1000 to 22000 is 21000ms > max_chunk_duration_ms (20000ms)
    chunks = merge_srt_cues(cues, max_chunk_duration_ms=20000)
    assert len(chunks) == 2
    assert chunks[0].text == "Sentence fragment one"
    assert chunks[0].cue_indices == [1]
    assert chunks[1].text == "sentence fragment two."
    assert chunks[1].cue_indices == [2]


def test_merge_srt_cues_multilingual_sentence_terminals():
    cues = [
        SRTCue(index=1, start_ms=0, end_ms=1500, text="これはテストで"),
        SRTCue(index=2, start_ms=1600, end_ms=3000, text="あります。"),
        SRTCue(index=3, start_ms=3100, end_ms=5000, text="次の一文です！"),
    ]
    chunks = merge_srt_cues(cues)
    assert len(chunks) == 2
    assert chunks[0].text == "これはテストで あります。"
    assert chunks[0].cue_indices == [1, 2]
    assert chunks[1].text == "次の一文です！"
    assert chunks[1].cue_indices == [3]


def test_merge_srt_cues_sentence_terminal_with_trailing_quotes():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=2000, text='He shouted, "Wait for me!"'),
        SRTCue(index=2, start_ms=2100, end_ms=3500, text="She nodded."),
    ]
    chunks = merge_srt_cues(cues)
    assert len(chunks) == 2
    assert chunks[0].text == 'He shouted, "Wait for me!"'
    assert chunks[0].cue_indices == [1]
    assert chunks[1].text == "She nodded."
    assert chunks[1].cue_indices == [2]


def test_merge_srt_cues_abbreviations_do_not_split():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=2000, text="We met with Dr."),
        SRTCue(index=2, start_ms=2100, end_ms=3500, text="Smith at the hospital."),
    ]
    chunks = merge_srt_cues(cues)
    assert len(chunks) == 1
    assert chunks[0].text == "We met with Dr. Smith at the hospital."
    assert chunks[0].cue_indices == [1, 2]


def test_merge_srt_cues_invalid_parameters():
    cues = [SRTCue(index=1, start_ms=1000, end_ms=2000, text="Test.")]
    with pytest.raises(ValueError, match="max_pause_ms"):
        merge_srt_cues(cues, max_pause_ms=-1)
    with pytest.raises(ValueError, match="max_chunk_duration_ms"):
        merge_srt_cues(cues, max_chunk_duration_ms=0)


def test_timed_chunk_duration_property():
    chunk = TimedChunk(
        chunk_index=0,
        start_ms=1500,
        end_ms=4500,
        text="Test duration.",
        cue_indices=[1],
    )
    assert chunk.duration_ms == 3000
