import json

import pytest

from app.domain.chapters import (
    ChapterManifest,
    ChapterRecord,
    ChapterStatus,
    display_filename,
    ensure_chapter_transition,
    sanitize_title,
)
from app.infrastructure.chapter_manifest_fs import FilesystemChapterManifest


def _record(**overrides: object) -> ChapterRecord:
    values: dict[str, object] = {
        "ordinal": 1,
        "kind": "chapter",
        "chapter_number": 1,
        "title_source": "第一章 少年",
        "title": "Chương 1 Thiếu niên",
        "chunk_first": 1,
        "chunk_last": 3,
    }
    values.update(overrides)
    return ChapterRecord(**values)  # type: ignore[arg-type]


def test_manifest_round_trip() -> None:
    manifest = ChapterManifest(
        version=1,
        max_chars=1200,
        chapter_aware=True,
        detector="v2",
        chapters=[_record(), _record(ordinal=2, chapter_number=2, chunk_first=4, chunk_last=6)],
    )
    restored = ChapterManifest.from_dict(json.loads(json.dumps(manifest.to_dict())))
    assert restored.detector == "v2"
    assert restored.chapters[1].ordinal == 2
    assert restored.chapters[0].status is ChapterStatus.PENDING
    assert restored.counts() == (2, 0, 0)


def test_status_transitions() -> None:
    record = _record()
    record.mark(ChapterStatus.GENERATING)
    record.mark(ChapterStatus.READY)
    record.mark(ChapterStatus.GENERATING)
    record.mark(ChapterStatus.FAILED)
    with pytest.raises(ValueError):
        ensure_chapter_transition(ChapterStatus.PENDING, ChapterStatus.READY)
    with pytest.raises(ValueError):
        record.mark(ChapterStatus.PENDING)


def test_missing_manifest_is_absent(tmp_path) -> None:
    assert FilesystemChapterManifest(tmp_path).load() is None


def test_corrupt_manifest_is_ignored(tmp_path) -> None:
    (tmp_path / "chapters.json").write_text("{", encoding="utf-8")
    assert FilesystemChapterManifest(tmp_path).load() is None


def test_manifest_load_parses_once_until_the_file_changes(tmp_path, monkeypatch) -> None:
    from app.infrastructure import chapter_manifest_fs

    store = FilesystemChapterManifest(tmp_path)
    store.save(
        ChapterManifest(
            version=1,
            max_chars=100,
            chapter_aware=True,
            detector="v2",
            chapters=[_record()],
        )
    )
    chapter_manifest_fs._CACHE.clear()
    calls = {"n": 0}
    real_loads = chapter_manifest_fs.json.loads

    def _counting(text: str, *args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(chapter_manifest_fs.json, "loads", _counting)
    assert store.load() is not None
    assert store.load() is not None
    assert calls["n"] == 1
    path = tmp_path / "chapters.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert store.load() is not None
    assert calls["n"] == 2


def test_manifest_write_is_atomic(tmp_path) -> None:
    store = FilesystemChapterManifest(tmp_path)
    store.save(
        ChapterManifest(
            version=1,
            max_chars=100,
            chapter_aware=True,
            detector="v2",
            chapters=[_record(status=ChapterStatus.READY, file="chapters/0001.mp3")],
        )
    )
    assert not list(tmp_path.glob(".chapters.json.*.tmp"))
    loaded = store.load()
    assert loaded is not None
    assert loaded.chapters[0].file == "chapters/0001.mp3"


@pytest.mark.parametrize(
    ("title", "ordinal", "expected"),
    [
        ("Chương 1 Thiếu niên", 1, "Chương 1 Thiếu niên"),
        ("a/b\\c:d*e?", 2, "a b c d e"),
        ("CON", 3, "Chapter 3"),
        ("", 4, "Chapter 4"),
        ("  ...  ", 5, "Chapter 5"),
        ("章" * 90, 6, "章" * 50),
    ],
)
def test_sanitize_title(title: str, ordinal: int, expected: str) -> None:
    assert sanitize_title(title, ordinal) == expected


def test_display_names_sort_by_ordinal() -> None:
    names = [
        display_filename(ordinal, f"Title {ordinal}", "mp3", total=12000)
        for ordinal in (1, 12, 100, 1000, 12000)
    ]
    assert names == sorted(names)
    assert names[0].startswith("00001 - ")
    assert names[-1].startswith("12000 - ")
    assert all(name.endswith(".mp3") for name in names)


def test_long_title_stays_under_byte_cap() -> None:
    safe = sanitize_title("字" * 200, 8)
    assert len(safe.encode("utf-8")) <= 150
    assert safe
