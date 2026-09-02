from app.domain.jobs import Job


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


class InMemorySourceStorage:
    def __init__(self) -> None:
        self.texts: dict[str, str] = {}

    async def write_source(self, job_id: str, text: str) -> None:
        self.texts[job_id] = text

    async def delete_job(self, job_id: str) -> None:
        self.texts.pop(job_id, None)


class InMemoryQueue:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class FailingQueue:
    async def enqueue(self, job_id: str) -> None:
        raise RuntimeError("queue unavailable")
