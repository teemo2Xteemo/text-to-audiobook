"""RQ worker entrypoints. Queue string: ``app.workers.process_job``."""

from app.workers.runner import process_job

__all__ = ["process_job"]
