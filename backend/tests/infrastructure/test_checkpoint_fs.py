import json
from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.infrastructure.checkpoint_fs import (
    CHECKPOINT_FILENAME,
    CHECKPOINT_JSONL_FILENAME,
    CheckpointFilesystem,
)


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
    assert (workspace / CHECKPOINT_JSONL_FILENAME).is_file()
    assert json.loads((workspace / CHECKPOINT_FILENAME).read_text(encoding="utf-8")) == loaded
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


def test_legacy_checkpoint_json_is_migrated_and_kept_until_compact(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    artifact = workspace / "chunks" / "chunk-001.translated.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("ok", encoding="utf-8")
    legacy = {
        "artifact_path": "chunks/chunk-001.translated.txt",
        "chunk_id": "chunk-001",
        "stage": "translated",
    }
    (workspace / CHECKPOINT_FILENAME).write_text(f"[{json.dumps(legacy)}]", encoding="utf-8")
    loaded = CheckpointFilesystem(workspace).load_raw()
    assert loaded == [legacy]
    assert (workspace / CHECKPOINT_JSONL_FILENAME).is_file()
    assert (workspace / CHECKPOINT_FILENAME).is_file()


def test_jsonl_skips_a_partial_trailing_line(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    line = json.dumps(
        {"artifact_path": "chunks/a.txt", "chunk_id": "chunk-001", "stage": "translated"}
    )
    (workspace / CHECKPOINT_JSONL_FILENAME).write_text(line + "\n{partial", encoding="utf-8")
    loaded = CheckpointFilesystem(workspace).load_raw()
    assert len(loaded) == 1
    assert loaded[0]["chunk_id"] == "chunk-001"


def test_jsonl_with_no_valid_lines_is_storage_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    (workspace / CHECKPOINT_JSONL_FILENAME).write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        CheckpointFilesystem(workspace).load_raw()
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
