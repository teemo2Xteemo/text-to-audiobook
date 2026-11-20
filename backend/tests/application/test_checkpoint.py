import json
from pathlib import Path

from app.application.pipeline.checkpoint import STAGE_TRANSLATED, CheckpointStore
from app.infrastructure.checkpoint_fs import (
    CHECKPOINT_FILENAME,
    CHECKPOINT_JSONL_FILENAME,
    CheckpointFilesystem,
)


def _artifact(workspace: Path, name: str) -> Path:
    path = workspace / "chunks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name, encoding="utf-8")
    return path


def test_record_appends_one_line_and_last_write_wins(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    store = CheckpointStore(storage=CheckpointFilesystem(workspace))
    first = _artifact(workspace, "one.txt")
    second = _artifact(workspace, "two.txt")
    store.record("chunk-001", STAGE_TRANSLATED, first)
    jsonl = workspace / CHECKPOINT_JSONL_FILENAME
    store.record("chunk-001", STAGE_TRANSLATED, second)
    lines = [line for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert store.load()[0].artifact_path.endswith("two.txt")
    reloaded = CheckpointStore(storage=CheckpointFilesystem(workspace))
    assert reloaded.load()[0].artifact_path.endswith("two.txt")
    assert reloaded.is_complete("chunk-001", STAGE_TRANSLATED)


def test_compact_rewrites_duplicates_and_drops_legacy_json(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    _artifact(workspace, "one.txt")
    legacy = [
        {
            "artifact_path": "chunks/stale.txt",
            "chunk_id": "chunk-001",
            "stage": STAGE_TRANSLATED,
        },
        {
            "artifact_path": "chunks/one.txt",
            "chunk_id": "chunk-001",
            "stage": STAGE_TRANSLATED,
        },
    ]
    (workspace / CHECKPOINT_FILENAME).write_text(json.dumps(legacy), encoding="utf-8")
    store = CheckpointStore(storage=CheckpointFilesystem(workspace))
    assert store.entry_count() == 1
    assert store.load()[0].artifact_path == "chunks/one.txt"
    store.compact()
    assert not (workspace / CHECKPOINT_FILENAME).is_file()
    lines = [
        line
        for line in (workspace / CHECKPOINT_JSONL_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert store.is_complete("chunk-001", STAGE_TRANSLATED)


def test_many_appends_stay_linear_in_the_file(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    store = CheckpointStore(storage=CheckpointFilesystem(workspace))
    folder = workspace / "chunks"
    folder.mkdir(parents=True)
    total = 20_000
    for index in range(total):
        path = folder / f"{index}.txt"
        if index == 0:
            path.write_text("x", encoding="utf-8")
        store.record(f"chunk-{index}", STAGE_TRANSLATED, path if index == 0 else folder / "0.txt")
    jsonl = workspace / CHECKPOINT_JSONL_FILENAME
    before = jsonl.stat().st_size
    store.record("chunk-extra", STAGE_TRANSLATED, folder / "0.txt")
    assert jsonl.stat().st_size - before < 400
    assert store.entry_count() == total + 1
    assert jsonl.stat().st_size < total * 400
