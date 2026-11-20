from app.application.pipeline.subtitle_sentences import (
    MAX_CUES_PER_SENTENCE,
    group_sentences,
    split_long_line,
    split_translation,
)
from app.domain.subtitles import CueSlot


def _cue(
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    translatable: bool = True,
) -> CueSlot:
    return CueSlot(
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        raw=text,
        translatable=translatable,
    )


def test_two_cue_sentence_grouped() -> None:
    cues = [
        _cue(0, 1000, "I think that"),
        _cue(1100, 2000, "we should go."),
        _cue(2100, 3000, "Now!"),
    ]
    assert group_sentences(cues) == [[0, 1], [2]]


def test_long_gap_breaks_group() -> None:
    assert group_sentences([_cue(0, 1000, "and then"), _cue(5000, 6000, "later")]) == [[0], [1]]


def test_unpunctuated_cues_group_is_capped() -> None:
    cues = [_cue(i * 1000, i * 1000 + 900, f"word {i}") for i in range(12)]
    groups = group_sentences(cues)
    assert all(len(group) <= MAX_CUES_PER_SENTENCE for group in groups)
    assert [index for group in groups for index in group] == list(range(12))


def test_sound_effect_cue_excluded_and_breaks_group() -> None:
    cues = [
        _cue(0, 1000, "Wait"),
        _cue(1000, 2000, "[Music]", translatable=False),
        _cue(2000, 3000, "here."),
    ]
    assert group_sentences(cues) == [[0], [2]]


def test_overlapping_cues_can_share_a_sentence() -> None:
    cues = [_cue(0, 2000, "Keep going"), _cue(1500, 3000, "please.")]
    assert group_sentences(cues) == [[0, 1]]


def test_split_by_weight_snaps_to_space() -> None:
    assert split_translation("Tôi nghĩ rằng chúng ta nên đi.", [12, 13]) == [
        "Tôi nghĩ rằng",
        "chúng ta nên đi.",
    ]


def test_split_without_spaces_cuts_characters() -> None:
    pieces = split_translation("我认为我们应该走了", [1, 1, 1])
    assert len(pieces) == 3
    assert "".join(pieces) == "我认为我们应该走了"
    assert all(pieces)


def test_split_short_translation_keeps_every_cue_nonempty() -> None:
    assert all(split_translation("Ok.", [10, 10, 10, 10]))


def test_split_long_line_respects_limit() -> None:
    assert all(len(piece) <= 84 for piece in split_long_line("Một câu rất dài. " * 20))
