from app.application.pipeline.subtitle_batch import (
    estimate_duration_ms,
    pack_batches,
    split_batch,
)


def test_split_batch_matches_count() -> None:
    assert split_batch(" A\n\nB \n", 2) == ["A", "B"]


def test_count_mismatch_returns_none() -> None:
    assert split_batch("A B", 2) is None


def test_pack_batches_respects_max_chars() -> None:
    assert pack_batches(["aaaa", "bbbb", "cccc"], max_chars=9) == [[0, 1], [2]]


def test_oversize_unit_is_its_own_batch() -> None:
    assert pack_batches(["abcdefghij", "bb"], max_chars=5) == [[0], [1]]


def test_duration_clamped() -> None:
    assert estimate_duration_ms("Hi") == 1000
    assert estimate_duration_ms("x " * 200) == 7000
