import pytest

from app.application.pipeline.timeline import (
    ScheduledItem,
    TimelineSchedule,
    schedule_srt_timeline,
)
from app.domain.srt import TimedChunk


def test_schedule_timeline_with_drift_absorption():
    chunks = [
        TimedChunk(
            chunk_index=0, start_ms=1000, end_ms=3000, text="First sentence.", cue_indices=[1]
        ),
        TimedChunk(
            chunk_index=1, start_ms=3000, end_ms=5000, text="Second sentence.", cue_indices=[2]
        ),
        TimedChunk(
            chunk_index=2, start_ms=8000, end_ms=10000, text="Third sentence.", cue_indices=[3]
        ),
    ]
    # Chunk 0 takes 2.5s (overruns by 0.5s into Chunk 1)
    # Chunk 1 takes 2.0s
    # Chunk 2 takes 2.0s
    durations = [2500, 2000, 2000]
    schedule = schedule_srt_timeline(chunks, durations)

    assert len(schedule.items) == 5
    # Initial silence 0 to 1000
    assert schedule.items[0].is_silence and schedule.items[0].duration_ms == 1000
    assert schedule.items[0].scheduled_start_ms == 0
    # Chunk 0 runs from 1000 to 3500
    assert schedule.items[1].chunk_index == 0 and schedule.items[1].scheduled_start_ms == 1000
    assert schedule.items[1].duration_ms == 2500
    # Chunk 1 shifted to start at 3500 (drift = 500ms), finishes at 5500
    assert schedule.items[2].chunk_index == 1 and schedule.items[2].scheduled_start_ms == 3500
    assert schedule.items[2].duration_ms == 2000
    # Nominal gap between chunk 1 (nominal end 5000) and chunk 2 (nominal start 8000) is 3000ms.
    # Chunk 1 overran to 5500ms (500ms drift). Absorbing 500ms drift reduces the pause to
    # 3000 - 500 = 2500ms silence, resynchronizing chunk 2 at exactly nominal 8000ms.
    assert schedule.items[3].is_silence and schedule.items[3].duration_ms == 2500
    assert schedule.items[3].scheduled_start_ms == 5500
    # Chunk 2 resynchronized at exact nominal 8000ms
    assert schedule.items[4].chunk_index == 2 and schedule.items[4].scheduled_start_ms == 8000
    assert schedule.items[4].duration_ms == 2000
    assert schedule.total_duration_ms == 10000


def test_schedule_timeline_starts_at_zero():
    chunks = [
        TimedChunk(chunk_index=0, start_ms=0, end_ms=2000, text="Starts at zero.", cue_indices=[1]),
        TimedChunk(chunk_index=1, start_ms=3000, end_ms=5000, text="Second.", cue_indices=[2]),
    ]
    durations = [2000, 2000]
    schedule = schedule_srt_timeline(chunks, durations)

    # No initial silence since chunk starts at 0ms
    assert len(schedule.items) == 3
    assert not schedule.items[0].is_silence
    assert schedule.items[0].chunk_index == 0
    assert schedule.items[0].scheduled_start_ms == 0
    assert schedule.items[0].duration_ms == 2000

    # Silence between 2000 and 3000 (1000ms)
    assert schedule.items[1].is_silence
    assert schedule.items[1].scheduled_start_ms == 2000
    assert schedule.items[1].duration_ms == 1000

    # Chunk 1 at 3000ms
    assert not schedule.items[2].is_silence
    assert schedule.items[2].chunk_index == 1
    assert schedule.items[2].scheduled_start_ms == 3000
    assert schedule.items[2].duration_ms == 2000
    assert schedule.total_duration_ms == 5000


def test_schedule_timeline_perfect_fit_no_drift():
    chunks = [
        TimedChunk(chunk_index=0, start_ms=1000, end_ms=2500, text="One.", cue_indices=[1]),
        TimedChunk(chunk_index=1, start_ms=4000, end_ms=5500, text="Two.", cue_indices=[2]),
    ]
    durations = [1500, 1500]
    schedule = schedule_srt_timeline(chunks, durations)

    assert len(schedule.items) == 4
    # Initial silence 0 to 1000
    assert schedule.items[0].is_silence and schedule.items[0].duration_ms == 1000
    # Chunk 0: 1000 to 2500
    assert schedule.items[1].chunk_index == 0 and schedule.items[1].scheduled_start_ms == 1000
    # Silence: 2500 to 4000 (1500ms)
    assert schedule.items[2].is_silence and schedule.items[2].duration_ms == 1500
    # Chunk 1: 4000 to 5500
    assert schedule.items[3].chunk_index == 1 and schedule.items[3].scheduled_start_ms == 4000
    assert schedule.total_duration_ms == 5500


def test_schedule_timeline_partial_drift_absorption():
    chunks = [
        TimedChunk(chunk_index=0, start_ms=1000, end_ms=3000, text="First.", cue_indices=[1]),
        TimedChunk(chunk_index=1, start_ms=3200, end_ms=5000, text="Second.", cue_indices=[2]),
    ]
    # Chunk 0 takes 2600ms (ends at 3600ms -> overruns nominal start 3200 by 400ms)
    # Next chunk 1 nominal start is 3200.
    # Drift = 400ms. Chunk 1 shifted to start at 3200 + 400 = 3600ms.
    # No silence between chunk 0 and chunk 1.
    durations = [2600, 1800]
    schedule = schedule_srt_timeline(chunks, durations)

    assert len(schedule.items) == 3
    assert schedule.items[0].is_silence and schedule.items[0].duration_ms == 1000
    assert schedule.items[1].chunk_index == 0 and schedule.items[1].scheduled_start_ms == 1000
    assert schedule.items[2].chunk_index == 1 and schedule.items[2].scheduled_start_ms == 3600
    assert schedule.total_duration_ms == 5400


def test_schedule_timeline_drift_safety_net_tempo():
    # Rapid dialogue where multiple chunks overrun and accumulate > 3000ms drift
    chunks = [
        TimedChunk(chunk_index=0, start_ms=1000, end_ms=2000, text="A.", cue_indices=[1]),
        TimedChunk(chunk_index=1, start_ms=2000, end_ms=3000, text="B.", cue_indices=[2]),
        TimedChunk(chunk_index=2, start_ms=3000, end_ms=4000, text="C.", cue_indices=[3]),
        TimedChunk(chunk_index=3, start_ms=4000, end_ms=5000, text="D.", cue_indices=[4]),
        TimedChunk(chunk_index=4, start_ms=12000, end_ms=13000, text="E.", cue_indices=[5]),
    ]
    # Chunk 0: takes 2500ms (drift = 1500)
    # Chunk 1: takes 2600ms (drift = 1500 + 1600 = 3100ms > 3000ms)
    # Chunk 2: enters with drift 3100ms > max_drift_ms (3000ms) -> safety net triggers atempo=1.12
    # Chunk 3: still in recovery mode -> atempo=1.12
    # Chunk 4: long pause absorbs drift -> tempo returns to 1.0
    durations = [2500, 2600, 2000, 2000, 1000]
    schedule = schedule_srt_timeline(chunks, durations, max_drift_ms=3000)

    # Chunk 0 and 1 normal tempo
    chunk_0_item = next(it for it in schedule.items if it.chunk_index == 0)
    chunk_1_item = next(it for it in schedule.items if it.chunk_index == 1)
    assert chunk_0_item.tempo_factor == 1.0
    assert chunk_1_item.tempo_factor == 1.0

    # Chunk 2 has tempo 1.12 applied
    chunk_2_item = next(it for it in schedule.items if it.chunk_index == 2)
    assert chunk_2_item.tempo_factor == 1.12
    assert chunk_2_item.duration_ms == int(round(2000 / 1.12))

    # Chunk 4 after recovery has tempo 1.0
    chunk_4_item = next(it for it in schedule.items if it.chunk_index == 4)
    assert chunk_4_item.tempo_factor == 1.0


def test_schedule_timeline_empty():
    schedule = schedule_srt_timeline([], [])
    assert schedule.items == []
    assert schedule.total_duration_ms == 0


def test_schedule_timeline_mismatched_durations():
    chunks = [TimedChunk(chunk_index=0, start_ms=0, end_ms=1000, text="A", cue_indices=[1])]
    with pytest.raises(ValueError, match="same length"):
        schedule_srt_timeline(chunks, [1000, 2000])


def test_schedule_timeline_negative_duration():
    chunks = [TimedChunk(chunk_index=0, start_ms=0, end_ms=1000, text="A", cue_indices=[1])]
    with pytest.raises(ValueError, match="duration"):
        schedule_srt_timeline(chunks, [-500])


def test_schedule_timeline_invalid_max_drift():
    chunks = [TimedChunk(chunk_index=0, start_ms=0, end_ms=1000, text="A", cue_indices=[1])]
    with pytest.raises(ValueError, match="max_drift_ms"):
        schedule_srt_timeline(chunks, [1000], max_drift_ms=0)


def test_scheduled_item_and_timeline_schedule_dataclasses():
    item = ScheduledItem(
        chunk_index=0,
        is_silence=False,
        scheduled_start_ms=1000,
        duration_ms=2000,
        tempo_factor=1.0,
        subtitle_text="Test sentence.",
    )
    assert item.chunk_index == 0
    assert not item.is_silence
    assert item.scheduled_start_ms == 1000
    assert item.duration_ms == 2000
    assert item.tempo_factor == 1.0
    assert item.subtitle_text == "Test sentence."

    schedule = TimelineSchedule(items=[item], total_duration_ms=3000)
    assert schedule.items == [item]
    assert schedule.total_duration_ms == 3000
