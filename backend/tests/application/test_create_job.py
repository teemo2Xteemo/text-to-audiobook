from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.application.jobs import CreateJobCommand, JobService
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.ports import LanguageDetector, TTSProvider
from app.domain.translation_profile import TranslationProfile
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from tests.fakes import FailingQueue, InMemoryJobStore, InMemoryQueue, InMemorySourceStorage


def _service(
    jobs: InMemoryJobStore | None = None,
    source: InMemorySourceStorage | None = None,
    queue: InMemoryQueue | FailingQueue | None = None,
    *,
    translation_provider: str = "fake",
    translation_model: str = "fake",
    available_profiles: list[TranslationProfile] | None = None,
    tts: TTSProvider | None = None,
    detector: LanguageDetector | None = None,
    language_confirm_threshold: float = 0.70,
    text_normalization_available: bool = False,
) -> tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue | FailingQueue]:
    store = jobs or InMemoryJobStore()
    storage = source or InMemorySourceStorage()
    queued = queue or InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=storage,
        queue=queued,
        output_bitrate_kbps=128,
        storage_path=Path("storage"),
        translation_provider=translation_provider,
        translation_model=translation_model,
        available_profiles=available_profiles,
        tts=tts,
        detector=detector,
        language_confirm_threshold=language_confirm_threshold,
        text_normalization_available=text_normalization_available,
    )
    return service, store, storage, queued


def test_create_stamps_lab_pin_not_the_request() -> None:
    service, store, _, _ = _service(
        translation_provider="ollama",
        translation_model="translategemma:4b",
    )
    command = CreateJobCommand(
        source_language="en-US",
        target_language="ja-JP",
        text="story",
    )
    assert "translation_provider" not in CreateJobCommand.__dataclass_fields__
    assert "translation_model" not in CreateJobCommand.__dataclass_fields__
    first = asyncio.run(service.create(command))
    second = asyncio.run(service.create(command))
    assert first.translation_provider == "ollama"
    assert first.translation_model == "translategemma:4b"
    assert second.translation_provider == "ollama"
    assert second.translation_model == "translategemma:4b"
    assert store.jobs[first.id].translation_model == "translategemma:4b"


def test_create_resolves_specified_translation_profile() -> None:
    profiles = [
        TranslationProfile(
            id="gemini-flash",
            provider="gemini",
            model="gemini-3.5-flash",
            label="Gemini",
            badge="ONLINE",
            description="desc",
            is_default=False,
        ),
        TranslationProfile(
            id="nllb-600m",
            provider="nllb",
            model="facebook/nllb-200-distilled-600M",
            label="NLLB",
            badge="OFFLINE",
            description="desc",
            is_default=True,
        ),
    ]
    service, store, _, _ = _service(
        translation_provider="nllb",
        translation_model="facebook/nllb-200-distilled-600M",
        available_profiles=profiles,
    )
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id="gemini-flash",
    )
    job = asyncio.run(service.create(cmd))
    assert job.translation_provider == "gemini"
    assert job.translation_model == "gemini-3.5-flash"


def test_create_rejects_unknown_translation_profile() -> None:
    service, _, _, _ = _service()
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id="non-existent-profile",
    )
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(service.create(cmd))
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT


def test_create_with_default_profile_id_matches_omitted_profile_id() -> None:
    profiles = [
        TranslationProfile(
            id="gemini-default",
            provider="gemini",
            model="gemini-1.5-flash",
            label="Gemini",
            badge="ONLINE",
            description="desc",
            is_default=True,
        ),
    ]
    service, _, _, _ = _service(
        translation_provider="gemini",
        translation_model="gemini-1.5-flash",
        available_profiles=profiles,
    )
    with_default = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="en-US",
                target_language="vi-VN",
                text="Hello story",
                translation_profile_id="gemini-default",
            )
        )
    )
    with_none = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="en-US",
                target_language="vi-VN",
                text="Hello story",
                translation_profile_id=None,
            )
        )
    )
    assert with_default.translation_provider == with_none.translation_provider == "gemini"
    assert with_default.translation_model == with_none.translation_model == "gemini-1.5-flash"


def test_create_with_whitespace_profile_id_falls_back_to_default() -> None:
    profiles = [
        TranslationProfile(
            id="default-prof",
            provider="nllb",
            model="nllb-default",
            label="NLLB",
            badge="OFFLINE",
            description="desc",
            is_default=True,
        ),
    ]
    service, _, _, _ = _service(
        translation_provider="nllb",
        translation_model="nllb-default",
        available_profiles=profiles,
    )
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="en-US",
                target_language="vi-VN",
                text="Hello story",
                translation_profile_id="   ",
            )
        )
    )
    assert job.translation_provider == "nllb"
    assert job.translation_model == "nllb-default"


def test_create_rejects_unsupported_language_for_profile() -> None:
    profiles = [
        TranslationProfile(
            id="nllb-600m",
            provider="nllb",
            model="nllb-600m",
            label="NLLB",
            badge="OFFLINE",
            description="desc",
            is_default=True,
            supported_languages=("en-US", "vi-VN"),
        ),
    ]
    service, _, _, _ = _service(
        translation_provider="nllb",
        translation_model="nllb-600m",
        available_profiles=profiles,
    )
    # Target unsupported
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="ja-JP",
        text="Hello story",
        translation_profile_id="nllb-600m",
    )
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(service.create(cmd))
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE

    # Source unsupported (when not "auto")
    cmd_src = CreateJobCommand(
        source_language="ja-JP",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id="nllb-600m",
    )
    with pytest.raises(DomainError) as exc_src:
        asyncio.run(service.create(cmd_src))
    assert exc_src.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_create_truncates_long_unknown_profile_id_in_error() -> None:
    service, _, _, _ = _service()
    long_id = "a" * 200
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id=long_id,
    )
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(service.create(cmd))
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT
    assert "a" * 64 in exc_info.value.message
    assert "a" * 65 not in exc_info.value.message


def test_create_with_fake_lab_stores_fake_pin() -> None:
    service, _, _, _ = _service()
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="en-US",
                target_language="ja-JP",
                text="story",
            )
        )
    )
    assert job.translation_provider == "fake"
    assert job.translation_model == "fake"


def test_retry_and_mark_failed_keep_pin_and_new_lab_does_not(tmp_path: Path) -> None:
    filesystem, _, _, _, service = _fs_retry_service(
        tmp_path,
        translation_provider="nllb",
        translation_model="facebook/nllb-200-distilled-600M",
    )
    running = replace(
        _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.TRANSLATING),
        chunk_current=2,
        chunk_total=5,
        translation_provider="ollama",
        translation_model="translategemma:4b",
    )
    asyncio.run(filesystem.save_job(running))
    asyncio.run(filesystem.write_source(running.id, "story to retry"))
    marked = asyncio.run(service.mark_failed(running.id, ErrorType.TIMEOUT, "timeout"))
    assert marked is not None
    assert marked.status is JobStatus.FAILED
    assert marked.translation_provider == "ollama"
    assert marked.translation_model == "translategemma:4b"

    retried = asyncio.run(service.retry(running.id))
    assert retried.translation_provider == "ollama"
    assert retried.translation_model == "translategemma:4b"
    assert retried.status is JobStatus.QUEUED
    assert retried.error_type is None
    assert retried.message is None
    assert retried.chunk_current == 0
    assert retried.chunk_total == 0

    later, _, _, _ = _service(
        translation_provider="nllb",
        translation_model="facebook/nllb-200-distilled-1.3B",
    )
    created = asyncio.run(
        later.create(
            CreateJobCommand(
                source_language="en-US",
                target_language="ja-JP",
                text="story",
            )
        )
    )
    assert created.translation_provider == "nllb"
    assert created.translation_model == "facebook/nllb-200-distilled-1.3B"
    stored = asyncio.run(filesystem.get_job(running.id))
    assert stored is not None
    assert stored.translation_provider == "ollama"
    assert stored.translation_model == "translategemma:4b"


def test_create_job_persists_queued_and_enqueues_id_only() -> None:
    service, store, storage, queue = _service()
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="ja-JP",
                target_language="en-US",
                text="  hello story  ",
                speed=1.25,
                output_format="wav",
            )
        )
    )
    assert job.status is JobStatus.QUEUED
    assert job.chunk_current == 0
    assert job.chunk_total == 0
    assert job.output_format is OutputFormat.WAV
    assert job.output_bitrate_kbps == 128
    assert job.speed == 1.25
    assert job.voice is None
    assert store.jobs[job.id] == job
    assert storage.texts[job.id] == "hello story"
    assert isinstance(queue, InMemoryQueue)
    assert queue.job_ids == [job.id]


def test_create_rejects_auto_target() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="zh-CN",
                    target_language="auto",
                    text="story",
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_rejects_speed_out_of_range() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ja-JP",
                    text="story",
                    speed=2.01,
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_rejects_unknown_output_format() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ja-JP",
                    text="story",
                    output_format="flac",
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_cleans_up_when_enqueue_fails() -> None:
    store = InMemoryJobStore()
    storage = InMemorySourceStorage()
    service, _, _, _ = _service(jobs=store, source=storage, queue=FailingQueue())
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ko-KR",
                    text="story",
                )
            )
        )
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
    assert store.jobs == {}
    assert storage.texts == {}


def test_get_returns_none_for_unknown_id() -> None:
    service, _, _, _ = _service()
    assert asyncio.run(service.get("missing")) is None


def test_retry_failed_job_requeues_same_id(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    failed = replace(
        _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
        chunk_current=2,
        chunk_total=5,
    )
    asyncio.run(filesystem.save_job(failed))
    asyncio.run(filesystem.write_source(failed.id, "story to retry"))
    cache.jobs[failed.id] = failed

    retried = asyncio.run(service.retry(failed.id))
    assert retried.id == failed.id
    assert retried.status is JobStatus.QUEUED
    assert retried.error_type is None
    assert retried.message is None
    assert retried.source_language == "en-US"
    assert retried.target_language == "ko-KR"
    assert retried.chunk_current == 0
    assert retried.chunk_total == 0
    assert asyncio.run(filesystem.read_source(failed.id)) == "story to retry"
    assert queue.job_ids == [failed.id]
    assert asyncio.run(filesystem.get_job(failed.id)).status is JobStatus.QUEUED
    assert asyncio.run(store.get(failed.id)).status is JobStatus.QUEUED


def test_retry_completed_is_rejected(tmp_path: Path) -> None:
    filesystem, _, _, _, service = _fs_retry_service(tmp_path)
    completed = _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(completed))
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.retry(completed.id))
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    assert exc.value.message == "job cannot be retried"


def test_retry_unknown_is_not_found(tmp_path: Path) -> None:
    _, _, _, _, service = _fs_retry_service(tmp_path)
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.retry("11111111-1111-1111-1111-111111111111"))
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    assert exc.value.message == "job not found"


def test_retry_reads_filesystem_status_not_job_store_cache(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    failed = replace(
        _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
    )
    asyncio.run(filesystem.save_job(failed))
    cache.jobs[failed.id] = replace(failed, status=JobStatus.COMPLETED)
    assert asyncio.run(store.get(failed.id)).status is JobStatus.FAILED

    retried = asyncio.run(service.retry(failed.id))
    assert retried.status is JobStatus.QUEUED
    assert queue.job_ids == [failed.id]
    assert asyncio.run(filesystem.get_job(failed.id)).status is JobStatus.QUEUED


def test_recover_enqueues_non_terminal_only(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    queue = InMemoryQueue()
    service = JobService(
        jobs=InMemoryJobStore(),
        source_storage=InMemorySourceStorage(),
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=filesystem,
    )
    queued = _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.QUEUED)
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.MERGING)
    failed = _fs_job("33333333-3333-3333-3333-333333333333", JobStatus.FAILED)
    completed = _fs_job("44444444-4444-4444-4444-444444444444", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(queued))
    asyncio.run(filesystem.save_job(running))
    asyncio.run(filesystem.save_job(failed))
    asyncio.run(filesystem.save_job(completed))

    recovered = asyncio.run(service.recover_in_progress())
    assert set(recovered) == {queued.id, running.id}
    assert set(queue.job_ids) == {queued.id, running.id}


def test_recover_reads_filesystem_status_not_job_store_cache(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = _MemoryJobCache()
    store = DualWriteJobStore(filesystem, cache)
    queue = InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=InMemorySourceStorage(),
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=filesystem,
    )
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.GENERATING_AUDIO)
    asyncio.run(filesystem.save_job(running))
    cache.jobs[running.id] = replace(running, status=JobStatus.COMPLETED)
    assert asyncio.run(store.get(running.id)).status is JobStatus.GENERATING_AUDIO

    recovered = asyncio.run(service.recover_in_progress())
    assert recovered == [running.id]
    assert queue.job_ids == [running.id]


def test_recover_marks_job_failed_when_recovery_attempts_exceeded(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path, max_recovery_attempts=2)
    running = replace(
        _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.GENERATING_AUDIO),
        recovery_attempts=2,
    )
    asyncio.run(filesystem.save_job(running))
    cache.jobs[running.id] = running

    recovered = asyncio.run(service.recover_in_progress())
    assert recovered == []
    assert queue.job_ids == []
    failed = asyncio.run(filesystem.get_job(running.id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.WORKER_FAILED
    assert failed.message == "job exceeded max crash recovery attempts"
    assert cache.jobs[running.id] == failed


def test_recover_increments_recovery_attempts_and_retry_resets_it(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path, max_recovery_attempts=2)
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.TRANSLATING)
    asyncio.run(filesystem.save_job(running))

    assert asyncio.run(service.recover_in_progress()) == [running.id]
    after_first = asyncio.run(filesystem.get_job(running.id))
    assert after_first is not None and after_first.recovery_attempts == 1
    assert cache.jobs[running.id].recovery_attempts == 1

    assert asyncio.run(service.recover_in_progress()) == [running.id]
    after_second = asyncio.run(filesystem.get_job(running.id))
    assert after_second is not None and after_second.recovery_attempts == 2

    # Third recovery attempt hits the cap (max_recovery_attempts=2) -> FAILED
    assert asyncio.run(service.recover_in_progress()) == []
    exhausted = asyncio.run(filesystem.get_job(running.id))
    assert exhausted is not None and exhausted.status is JobStatus.FAILED

    # Explicit user retry resets recovery_attempts to 0
    retried = asyncio.run(service.retry(running.id))
    assert retried.status is JobStatus.QUEUED
    assert retried.recovery_attempts == 0
    assert len(queue.job_ids) == 3


def test_recover_queued_job_does_not_burn_recovery_attempts_or_fail(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path, max_recovery_attempts=2)
    queued = _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.QUEUED)
    asyncio.run(filesystem.save_job(queued))

    for _ in range(4):
        assert asyncio.run(service.recover_in_progress()) == [queued.id]

    reloaded = asyncio.run(filesystem.get_job(queued.id))
    assert reloaded is not None
    assert reloaded.status is JobStatus.QUEUED
    assert reloaded.recovery_attempts == 0
    assert cache.jobs[queued.id].status is JobStatus.QUEUED
    assert cache.jobs[queued.id].recovery_attempts == 0
    assert len(queue.job_ids) == 4


def test_recover_skips_actively_locked_job_without_incrementing_recovery_attempts(
    tmp_path: Path,
) -> None:
    import fcntl

    filesystem, _cache, _store, queue, service = _fs_retry_service(
        tmp_path, max_recovery_attempts=2
    )
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.TRANSLATING)
    asyncio.run(filesystem.save_job(running))

    workspace = tmp_path / "jobs" / running.id
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = workspace / ".lock"

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Worker holds lock: recover must skip, not re-enqueue, not increment recovery_attempts
        recovered = asyncio.run(service.recover_in_progress())
        assert recovered == []
        assert queue.job_ids == []

        fresh = asyncio.run(filesystem.get_job(running.id))
        assert fresh is not None
        assert fresh.recovery_attempts == 0
        assert fresh.status is JobStatus.TRANSLATING

        # Worker process finishes/exits, releasing the flock
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # Now unlocked: recover picks up the job and increments recovery_attempts
    recovered_after = asyncio.run(service.recover_in_progress())
    assert recovered_after == [running.id]
    assert queue.job_ids == [running.id]

    fresh_after = asyncio.run(filesystem.get_job(running.id))
    assert fresh_after is not None
    assert fresh_after.recovery_attempts == 1


def test_mark_failed_from_non_terminal_updates_filesystem_and_cache(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("66666666-6666-6666-6666-666666666666", JobStatus.GENERATING_AUDIO)
    asyncio.run(filesystem.save_job(running))
    cache.jobs[running.id] = replace(running, status=JobStatus.TRANSLATING)

    failed = asyncio.run(
        service.mark_failed(
            running.id, ErrorType.TIMEOUT, "job exceeded the worker timeout (1800s)"
        )
    )
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.TIMEOUT
    assert failed.message == "job exceeded the worker timeout (1800s)"
    assert asyncio.run(filesystem.get_job(running.id)) == failed
    assert cache.jobs[running.id] == failed
    assert queue.job_ids == []


def test_mark_failed_is_noop_when_already_failed(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    existing = replace(
        _fs_job("77777777-7777-7777-7777-777777777777", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
    )
    asyncio.run(filesystem.save_job(existing))
    cache.jobs[existing.id] = existing

    result = asyncio.run(service.mark_failed(existing.id, ErrorType.TIMEOUT, "timeout"))
    assert result == existing
    assert asyncio.run(filesystem.get_job(existing.id)) == existing
    assert cache.jobs[existing.id] == existing
    assert queue.job_ids == []


def test_mark_failed_is_noop_when_completed(tmp_path: Path) -> None:
    filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    done = _fs_job("88888888-8888-8888-8888-888888888888", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(done))

    result = asyncio.run(service.mark_failed(done.id, ErrorType.TIMEOUT, "timeout"))
    assert result is not None
    assert result.status is JobStatus.COMPLETED
    assert result.error_type is None
    assert asyncio.run(filesystem.get_job(done.id)) == done
    assert queue.job_ids == []


def test_mark_failed_missing_job_returns_none(tmp_path: Path) -> None:
    _filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    result = asyncio.run(
        service.mark_failed("99999999-9999-9999-9999-999999999999", ErrorType.TIMEOUT, "timeout")
    )
    assert result is None
    assert queue.job_ids == []


def test_recover_does_not_enqueue_after_mark_failed(tmp_path: Path) -> None:
    filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", JobStatus.TRANSLATING)
    asyncio.run(filesystem.save_job(running))
    asyncio.run(service.mark_failed(running.id, ErrorType.TIMEOUT, "timeout"))

    recovered = asyncio.run(service.recover_in_progress())
    assert recovered == []
    assert queue.job_ids == []


def test_get_returns_filesystem_failed_not_stale_non_terminal_cache(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("dddddddd-dddd-dddd-dddd-dddddddddddd", JobStatus.TRANSLATING)
    failed = replace(
        running,
        status=JobStatus.FAILED,
        error_type=ErrorType.TIMEOUT,
        message="timeout",
    )
    asyncio.run(filesystem.save_job(failed))
    cache.jobs[running.id] = running
    assert asyncio.run(service.get(running.id)) == failed
    assert queue.job_ids == []


def test_get_returns_filesystem_queued_not_stale_failed_cache(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    queued = _fs_job("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", JobStatus.QUEUED)
    failed = replace(
        queued,
        status=JobStatus.FAILED,
        error_type=ErrorType.TIMEOUT,
        message="timeout",
    )
    asyncio.run(filesystem.save_job(queued))
    cache.jobs[queued.id] = failed
    assert asyncio.run(service.get(queued.id)) == queued
    assert queue.job_ids == []


def _fs_retry_service(
    tmp_path: Path,
    *,
    translation_provider: str = "fake",
    translation_model: str = "fake",
    max_recovery_attempts: int = 3,
) -> tuple[FilesystemJobStorage, _MemoryJobCache, DualWriteJobStore, InMemoryQueue, JobService]:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = _MemoryJobCache()
    store = DualWriteJobStore(filesystem, cache)
    queue = InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=filesystem,
        max_recovery_attempts=max_recovery_attempts,
        translation_provider=translation_provider,
        translation_model=translation_model,
    )
    return filesystem, cache, store, queue, service


def _fs_job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        status=status,
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )


class _MemoryJobCache:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


def test_resolve_voice_family_fallback_and_cantonese_guard() -> None:
    from app.application.pipeline.tts import resolve_voice
    from app.domain.audio import Voice
    from app.domain.errors import DomainError, ErrorType
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider(
        voices=[
            Voice(id="en-GB-SoniaNeural", name="Sonia", language="en-GB", gender="female"),
            Voice(id="zh-HK-HiuGaaiNeural", name="HiuGaai", language="zh-HK", gender="female"),
            Voice(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", language="zh-CN", gender="female"),
        ]
    )

    # Family fallback: en-US falls back to en-GB
    voice = resolve_voice(fake_tts, "en-US", None)
    assert voice == "en-GB-SoniaNeural"

    # Cantonese guard: zh-HK never falls back to zh-CN
    fake_cantonese_only = FakeTTSProvider(
        voices=[
            Voice(id="zh-HK-HiuGaaiNeural", name="HiuGaai", language="zh-HK", gender="female"),
        ]
    )
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_cantonese_only, "zh-CN", None)
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_resolve_voice_cantonese_text_does_not_fall_back_to_mandarin() -> None:
    from app.application.pipeline.tts import resolve_voice
    from app.domain.audio import Voice
    from app.domain.errors import DomainError, ErrorType
    from app.providers.fake import FakeTTSProvider

    fake_mandarin_only = FakeTTSProvider(
        voices=[
            Voice(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", language="zh-CN", gender="female"),
        ]
    )
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_mandarin_only, "zh-HK", None)
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_resolve_voice_explicit_cross_cantonese_mandarin_rejected() -> None:
    from app.application.pipeline.tts import resolve_voice
    from app.domain.audio import Voice
    from app.domain.errors import DomainError, ErrorType
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider(
        voices=[
            Voice(id="zh-HK-HiuGaaiNeural", name="HiuGaai", language="zh-HK", gender="female"),
            Voice(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", language="zh-CN", gender="female"),
        ]
    )
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_tts, "zh-CN", "zh-HK-HiuGaaiNeural")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE

    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_tts, "zh-HK", "zh-CN-XiaoxiaoNeural")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_resolve_voice_explicit_voice_selection_and_invalid() -> None:
    from app.application.pipeline.tts import resolve_voice, select_voice
    from app.domain.audio import Voice
    from app.domain.errors import DomainError, ErrorType
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider(
        voices=[
            Voice(id="en-GB-SoniaNeural", name="Sonia", language="en-GB", gender="female"),
            Voice(id="en-US-JennyNeural", name="Jenny", language="en-US", gender="female"),
        ]
    )

    # Exact selection
    assert resolve_voice(fake_tts, "en-US", "en-US-JennyNeural") == "en-US-JennyNeural"
    # Same family explicit selection
    assert resolve_voice(fake_tts, "en-US", "en-GB-SoniaNeural") == "en-GB-SoniaNeural"
    # Missing voice
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_tts, "en-US", "missing-voice")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE
    # Incompatible family
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_tts, "ja-JP", "en-US-JennyNeural")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE

    # select_voice alias compatibility with 2 and 3 args
    assert select_voice(fake_tts, "en-CA") == "en-US-JennyNeural"
    assert select_voice(fake_tts, "en-CA", "en-GB-SoniaNeural") == "en-GB-SoniaNeural"


def test_resolve_voice_skips_mandarin_dialects_in_auto_fallback_unless_explicit() -> None:
    from app.application.pipeline.tts import resolve_voice
    from app.domain.audio import Voice
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider(
        voices=[
            Voice(id="zh-CN-liaoning-XiaobeiNeural", language="zh-CN-liaoning"),
            Voice(id="zh-TW-HsiaoChenNeural", language="zh-TW"),
        ]
    )

    # Automatic fallback skips liaoning dialect and picks zh-TW
    assert resolve_voice(fake_tts, "zh-CN", None) == "zh-TW-HsiaoChenNeural"

    # Explicit selection allows the dialect voice
    assert (
        resolve_voice(fake_tts, "zh-CN", "zh-CN-liaoning-XiaobeiNeural")
        == "zh-CN-liaoning-XiaobeiNeural"
    )


def test_fake_tts_languages_and_voice_inspection() -> None:
    from app.domain.audio import Voice
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider()
    assert len(fake_tts.languages()) > 0
    assert len(fake_tts.all_voices()) > 0

    fake_tts_custom = FakeTTSProvider(
        voices=[
            Voice(id="v1", language="en-US"),
            Voice(id="v2", language="en-GB"),
        ]
    )
    assert fake_tts_custom.languages() == {"en-US", "en-GB"}
    assert [v.id for v in fake_tts_custom.all_voices()] == ["v1", "v2"]


@pytest.fixture
def job_service() -> JobService:
    service, _, _, _ = _service()
    return service


def test_create_narrate_job_validation(job_service: JobService) -> None:
    from app.domain.errors import DomainError, ErrorType
    from app.domain.jobs import JobMode

    # Narrate rejects translation profile
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            job_service.create(
                CreateJobCommand(
                    text="Hello",
                    source_language="en-US",
                    target_language="en-US",
                    mode=JobMode.NARRATE,
                    translation_profile_id="some-profile",
                )
            )
        )
    assert exc.value.error_type == ErrorType.INVALID_INPUT


def test_create_narrate_job_auto_source_without_detector_raises() -> None:
    from app.domain.jobs import JobMode

    service, _, _, _ = _service(detector=None)
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    text="Hello world",
                    source_language="auto",
                    target_language="",
                    mode=JobMode.NARRATE,
                )
            )
        )
    assert exc.value.error_type == ErrorType.INVALID_INPUT
    assert "auto language detection unavailable" in exc.value.message


def test_create_narrate_job_auto_source_resolves_and_sets_target_language() -> None:
    from app.domain.jobs import JobMode
    from app.domain.languages import LanguageDetection

    class _FakeDetector:
        async def detect(self, text: str) -> LanguageDetection:
            return LanguageDetection(language_code="vi-VN", confidence=0.99)

    service, _, _, _ = _service(detector=_FakeDetector())
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                text="Xin chào thế giới",
                source_language="auto",
                target_language="",
                mode=JobMode.NARRATE,
            )
        )
    )
    assert job.mode == JobMode.NARRATE
    assert job.source_language == "vi-VN"
    assert job.target_language == "vi-VN"
    assert job.translation_provider is None
    assert job.translation_model is None


def test_create_narrate_job_target_language_mismatch_raises() -> None:
    from app.domain.jobs import JobMode

    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    text="Hello world",
                    source_language="en-US",
                    target_language="ja-JP",
                    mode=JobMode.NARRATE,
                )
            )
        )
    assert exc.value.error_type == ErrorType.INVALID_INPUT
    assert "target_language must match source_language" in exc.value.message


def test_create_narrate_job_resolves_voice_via_tts() -> None:
    from app.domain.audio import Voice
    from app.domain.jobs import JobMode
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider(
        voices=[
            Voice(id="vi-VN-HoaiMyNeural", name="HoaiMy", language="vi-VN", gender="female"),
        ]
    )
    service, _, _, _ = _service(tts=fake_tts)
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                text="Xin chào",
                source_language="vi-VN",
                target_language="vi-VN",
                mode=JobMode.NARRATE,
            )
        )
    )
    assert job.voice == "vi-VN-HoaiMyNeural"


def test_create_narrate_job_low_detection_confidence_raises() -> None:
    from app.domain.jobs import JobMode
    from app.domain.languages import LanguageDetection

    class _LowConfidenceDetector:
        async def detect(self, text: str) -> LanguageDetection:
            return LanguageDetection(language_code="en-US", confidence=0.45)

        async def detect_candidates(self, text: str) -> list[tuple[str, float]]:
            return [("en-US", 0.45)]

    service, _, _, _ = _service(
        detector=_LowConfidenceDetector(),
        language_confirm_threshold=0.70,
    )
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    text="Ambiguous text snippet",
                    source_language="auto",
                    target_language="",
                    mode=JobMode.NARRATE,
                )
            )
        )
    assert exc.value.error_type == ErrorType.INVALID_INPUT
    assert "below threshold" in exc.value.message


def test_create_normalize_text_requires_configured_normalizer() -> None:
    command = CreateJobCommand(
        text="Hello 20",
        source_language="en-US",
        target_language="ko-KR",
        normalize_text=True,
    )
    service, store, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.create(command))
    assert exc.value.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR
    assert store.jobs == {}

    service, _, _, _ = _service(text_normalization_available=True)
    job = asyncio.run(service.create(command))
    assert job.normalize_text is True
    assert Job.from_dict(job.to_dict()).normalize_text is True
