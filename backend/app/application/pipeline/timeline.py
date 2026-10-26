from dataclasses import dataclass

from app.domain.srt import TimedChunk

SAFETY_NET_TEMPO_FACTOR = 1.12
RECOVERY_DRIFT_THRESHOLD_MS = 1000


@dataclass(frozen=True)
class ScheduledItem:
    chunk_index: int | None
    is_silence: bool
    scheduled_start_ms: int
    duration_ms: int
    tempo_factor: float = 1.0
    subtitle_text: str | None = None


@dataclass(frozen=True)
class TimelineSchedule:
    items: list[ScheduledItem]
    total_duration_ms: int
    max_drift_ms: int = 0
    final_drift_ms: int = 0


def schedule_srt_timeline(
    chunks: list[TimedChunk],
    chunk_durations_ms: list[int],
    max_drift_ms: int = 3000,
) -> TimelineSchedule:
    """Schedules TimedChunks onto a synchronized timeline with drift absorption.

    Drift absorption shifts downstream speech during overruns and absorbs
    accumulated drift into available pause intervals without robotic speedups.
    If accumulated drift exceeds max_drift_ms, subtle tempo adjustment (1.12x)
    is applied to speech chunks until drift recovers.
    """
    if len(chunks) != len(chunk_durations_ms):
        raise ValueError(
            f"chunks and chunk_durations_ms must have same length: "
            f"{len(chunks)} != {len(chunk_durations_ms)}"
        )
    if max_drift_ms <= 0:
        raise ValueError("max_drift_ms must be > 0")
    for d in chunk_durations_ms:
        if d < 0:
            raise ValueError(f"chunk duration must be >= 0: {d}")

    if not chunks:
        return TimelineSchedule(items=[], total_duration_ms=0)

    items: list[ScheduledItem] = []
    accumulated_drift_ms = 0
    peak_drift_ms = 0
    tempo_active = False

    # 1. Initial silence before first chunk if it starts after t=0
    first_chunk = chunks[0]
    if first_chunk.start_ms > 0:
        items.append(
            ScheduledItem(
                chunk_index=None,
                is_silence=True,
                scheduled_start_ms=0,
                duration_ms=first_chunk.start_ms,
                tempo_factor=1.0,
                subtitle_text=None,
            )
        )

    for i, chunk in enumerate(chunks):
        raw_duration = chunk_durations_ms[i]

        # Safety net: if accumulated drift exceeds threshold, apply gentle tempo (1.12x)
        recovery_target = min(RECOVERY_DRIFT_THRESHOLD_MS, max_drift_ms // 3)
        if accumulated_drift_ms > max_drift_ms:
            tempo_active = True
        elif accumulated_drift_ms < recovery_target:
            tempo_active = False

        if tempo_active:
            tempo_factor = SAFETY_NET_TEMPO_FACTOR
            effective_duration = int(round(raw_duration / tempo_factor)) if raw_duration > 0 else 0
        else:
            tempo_factor = 1.0
            effective_duration = raw_duration

        # Scheduled start time: nominal start shifted by accumulated drift
        actual_start = chunk.start_ms + accumulated_drift_ms
        actual_end = actual_start + effective_duration

        items.append(
            ScheduledItem(
                chunk_index=chunk.chunk_index,
                is_silence=False,
                scheduled_start_ms=actual_start,
                duration_ms=effective_duration,
                tempo_factor=tempo_factor,
                subtitle_text=chunk.text,
            )
        )

        # Pause calculation before next chunk (if any)
        if i < len(chunks) - 1:
            next_chunk = chunks[i + 1]
            nominal_next_start_with_drift = next_chunk.start_ms + accumulated_drift_ms

            if actual_end > nominal_next_start_with_drift:
                # Speech overruns past scheduled boundary into next cue
                accumulated_drift_ms = actual_end - next_chunk.start_ms
                peak_drift_ms = max(peak_drift_ms, accumulated_drift_ms)
                # Silence gap before next chunk is 0ms (no silence item)
            else:
                # Speech finished before next cue starts with drift
                raw_pause = nominal_next_start_with_drift - actual_end
                if accumulated_drift_ms > 0:
                    absorbed = min(raw_pause, accumulated_drift_ms)
                    accumulated_drift_ms -= absorbed
                    final_silence_ms = raw_pause - absorbed
                else:
                    final_silence_ms = raw_pause

                if final_silence_ms > 0:
                    items.append(
                        ScheduledItem(
                            chunk_index=None,
                            is_silence=True,
                            scheduled_start_ms=actual_end,
                            duration_ms=final_silence_ms,
                            tempo_factor=1.0,
                            subtitle_text=None,
                        )
                    )
        else:
            end_drift = max(0, actual_end - chunk.end_ms)
            peak_drift_ms = max(peak_drift_ms, end_drift)
            accumulated_drift_ms = end_drift

    total_duration_ms = items[-1].scheduled_start_ms + items[-1].duration_ms if items else 0
    return TimelineSchedule(
        items=items,
        total_duration_ms=total_duration_ms,
        max_drift_ms=peak_drift_ms,
        final_drift_ms=accumulated_drift_ms,
    )
