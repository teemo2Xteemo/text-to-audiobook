from redis import Redis
from rq import Queue

from app.application.jobs import JobService
from app.config.settings import Settings
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from app.infrastructure.redis_job_store import RedisJobCache
from app.infrastructure.rq_queue import RQ_QUEUE_NAME, RQJobQueue


def build_job_service(settings: Settings) -> JobService:
    cache_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    queue_client = Redis.from_url(str(settings.redis_url))
    filesystem = FilesystemJobStorage(settings.storage_path)
    store = DualWriteJobStore(filesystem, RedisJobCache(cache_client))
    queue = RQJobQueue(Queue(RQ_QUEUE_NAME, connection=queue_client))
    return JobService(
        jobs=store,
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=settings.output_bitrate_kbps,
    )
