from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job

_STATUS_FILENAME = "status.json"
_SOURCE_FILENAME = "source.txt"


class FilesystemJobStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def write_source(self, job_id: str, text: str) -> None:
        directory = self._job_dir(job_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / _SOURCE_FILENAME).write_text(text, encoding="utf-8")
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write source text") from exc

    async def read_source(self, job_id: str) -> str:
        path = self._job_dir(job_id) / _SOURCE_FILENAME
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "source text not found") from exc
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read source text") from exc

    async def save_job(self, job: Job) -> None:
        directory = self._job_dir(job.id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
            (directory / _STATUS_FILENAME).write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write job status") from exc

    async def get_job(self, job_id: str) -> Job | None:
        path = self._job_dir(job_id) / _STATUS_FILENAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Job.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read job status") from exc

    async def delete_job(self, job_id: str) -> None:
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    async def list_job_ids(self) -> list[str]:
        jobs_root = self._root / "jobs"
        if not jobs_root.is_dir():
            return []
        jobs_root = jobs_root.resolve()
        ids: list[str] = []
        for entry in jobs_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                uuid.UUID(entry.name)
            except ValueError:
                continue
            directory = entry.resolve()
            if not directory.is_relative_to(jobs_root):
                continue
            if not (directory / _STATUS_FILENAME).is_file():
                continue
            ids.append(entry.name)
        return sorted(ids)

    def _job_dir(self, job_id: str) -> Path:
        jobs_root = (self._root / "jobs").resolve()
        directory = (jobs_root / job_id).resolve()
        if not directory.is_relative_to(jobs_root):
            raise DomainError(ErrorType.INVALID_INPUT, "invalid job_id")
        return directory
