from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache


def test_cache_roundtrip_copy(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    source = tmp_path / "in.txt"
    source.write_text("hello", encoding="utf-8")
    key = "a" * 64
    cache.put("translation", key, source)
    dest = tmp_path / "out.txt"
    assert cache.get("translation", key, dest) is True
    assert dest.read_text(encoding="utf-8") == "hello"
    assert (tmp_path / "cache" / "translation" / key).is_file()


def test_get_copy_is_independent_of_cache_blob(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    source = tmp_path / "in.txt"
    source.write_text("hello", encoding="utf-8")
    key = "a" * 64
    cache.put("translation", key, source)
    dest = tmp_path / "job" / "chunk-001.translated.txt"
    assert cache.get("translation", key, dest) is True
    blob = tmp_path / "cache" / "translation" / key
    dest_stat = dest.stat()
    blob_stat = blob.stat()
    assert dest_stat.st_ino != blob_stat.st_ino
    assert dest_stat.st_nlink == 1
    assert blob_stat.st_nlink == 1
    dest.write_text("mutated", encoding="utf-8")
    assert blob.read_text(encoding="utf-8") == "hello"


def test_empty_blob_is_miss(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    key = "b" * 64
    blob = tmp_path / "cache" / "translation" / key
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"")
    dest = tmp_path / "out.txt"
    assert cache.get("translation", key, dest) is False
    assert not dest.exists()


def test_missing_blob_is_miss(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    dest = tmp_path / "out.txt"
    assert cache.get("translation", "c" * 64, dest) is False


def test_put_skips_empty_source(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    cache.put("tts", "d" * 64, empty)
    assert not (tmp_path / "cache" / "tts" / ("d" * 64)).exists()


def test_invalid_key_is_rejected(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    dest = tmp_path / "out.txt"
    with pytest.raises(DomainError) as exc:
        cache.get("translation", "../secret", dest)
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_unknown_operation_is_rejected(tmp_path: Path) -> None:
    cache = FilesystemArtifactCache(tmp_path / "cache")
    source = tmp_path / "in.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        cache.put("narration", "e" * 64, source)
    assert exc.value.error_type is ErrorType.INVALID_INPUT
