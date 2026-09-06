from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.infrastructure.checkpoint_fs import CHECKPOINT_FILENAME, CheckpointFilesystem


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "jobs" / "11111111-1111-1111-1111-111111111111"
    workspace.mkdir(parents=True)
    fs = CheckpointFilesystem(workspace)
    artifact = workspace / "chunks" / "chunk-001.translated.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("ok", encoding="utf-8")
    relative = fs.relative(artifact)
    fs.save_raw([{"artifact_path": relative, "chunk_id": "chunk-001", "stage": "translated"}])
    loaded = fs.load_raw()
    assert loaded == [
        {
            "artifact_path": "chunks/chunk-001.translated.txt",
            "chunk_id": "chunk-001",
            "stage": "translated",
        }
    ]
    assert (workspace / CHECKPOINT_FILENAME).is_file()
    assert fs.resolve(relative) == artifact.resolve()


def test_missing_checkpoint_is_empty(tmp_path: Path) -> None:
    fs = CheckpointFilesystem(tmp_path / "missing")
    assert fs.load_raw() == []


def test_corrupt_checkpoint_is_storage_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    (workspace / CHECKPOINT_FILENAME).write_text("{not-json", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        CheckpointFilesystem(workspace).load_raw()
    assert exc.value.error_type is ErrorType.STORAGE_FAILED


def test_relative_rejects_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        CheckpointFilesystem(workspace).relative(outside)
    assert exc.value.error_type is ErrorType.STORAGE_FAILED


def test_resolve_rejects_parent_segments(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    with pytest.raises(DomainError) as exc:
        CheckpointFilesystem(workspace).resolve("../secret.txt")
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
