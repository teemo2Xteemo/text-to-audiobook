# Ponytail Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the actionable findings (#1–#9) from the `/ponytail:ponytail-audit` report across API dependency caching, temporary TTS file cleanup, non-blocking async I/O, worker concurrency, checkpoint deduplication and in-memory caching, API validation deduplication, and backend `.srt` subtitle support.

**Architecture:** We make minimal, targeted changes in `backend/app/{api,application,config,infrastructure,providers,workers}` while preserving all hexagonal domain boundaries (`api → application → domain ← providers/infrastructure`). Note that Finding #10 (collapsing `translate.py`, `narrate.py`, `normalize.py`, and `merge.py` into `orchestrator.py`) is intentionally excluded because `.cursor/rules/01-architecture.mdc` explicitly mandates separate modules per pipeline stage.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, Redis, RQ, pytest, Ruff.

**Spec:** `/ponytail:ponytail-audit` report in conversation `26f93455-cdaa-4f0c-9335-d3525abf0b63` and `.cursor/rules/01-architecture.mdc`.

## Global Constraints

- **Language-agnostic:** never hard-code a source language, target language, or language pair in business logic. Use `source_language`, `target_language`, and provider capabilities.
- **Provider ports:** domain and application code call `TranslationProvider`, `TTSProvider`, `CheckpointStorage`, and `JobStore` interfaces, never vendor SDKs or `app.infrastructure.*` modules directly.
- **Pipeline stages (separate modules):** `.cursor/rules/01-architecture.mdc` requires separate modules for `parse`, `chunk`, `translate`, `narrate`, `tts`, `normalize`, and `merge`.
- **No global mutable clients:** inject dependencies via FastAPI `app.state` and constructor arguments; do not use module-level `@lru_cache` singletons.
- **Verification commands in sandbox:** run pytest with `-p no:cacheprovider -m "not integration"` and ruff with `--no-cache`.

## Review Focus

1. **Repeated HTTP polling (`GET /api/jobs/{id}` and `GET /api/capabilities`) when `app.state` starts unpopulated:** `build_job_service` and `build_capabilities_service` must run once per app instance and cache on `request.app.state`.
2. **Failed or zero-byte TTS synthesis output in `storage/.provider-tmp/tts/`:** failed or empty provider artifacts must be unlinked so orphan files do not accumulate on retries or errors.
3. **Checkpoint `save_raw` failure mid-run:** `CheckpointStore.record` must not update its in-memory `_entries` cache if `self._fs.save_raw(...)` raises `DomainError`.
4. **`WORKER_CONCURRENCY > 1` termination:** when the foreground RQ worker exits in `_listen`, any spawned child worker processes must be terminated cleanly in a `finally` block.
5. **`.srt` file containing only sequence numbers and timecodes (no dialogue text):** stripping SRT metadata must leave an empty string that `parse_source` rejects with `DomainError(ErrorType.INVALID_INPUT, "text is required")`.

---

### Task 1: Cache `JobService` and `CapabilitiesService` on `request.app.state` (Finding #1)

**Files:**
- Modify: `backend/app/api/deps.py:13-24`
- Test: `backend/tests/api/test_jobs.py`, `backend/tests/api/test_capabilities.py`

**Interfaces:**
- Consumes: `build_job_service(settings: Settings) -> JobService`, `build_capabilities_service(settings: Settings) -> CapabilitiesService` from `app.config.factory`
- Produces: `get_job_service(request: Request) -> JobService` and `get_capabilities_service(request: Request) -> CapabilitiesService` in `backend/app/api/deps.py` that cache the built instance on `request.app.state.job_service` and `request.app.state.capabilities_service`

- [ ] **Step 1: Write the failing tests in `backend/tests/api/test_jobs.py` and `backend/tests/api/test_capabilities.py`**

Append to `backend/tests/api/test_jobs.py`:
```python
def test_get_job_service_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    service, _, _, _ = memory
    calls = 0

    def _fake_build(settings: Settings) -> JobService:
        nonlocal calls
        del settings
        calls += 1
        return service

    monkeypatch.setattr("app.api.deps.build_job_service", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/jobs", json=VALID_BODY).json()
        client.get(f"/api/jobs/{created['job_id']}")
        client.get(f"/api/jobs/{created['job_id']}")
    assert calls == 1
    assert app.state.job_service is service
```

Append to `backend/tests/api/test_capabilities.py`:
```python
def test_get_capabilities_service_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translation = FakeTranslationProvider(languages=["en-US"])
    tts = FakeTTSProvider(
        voices=[Voice(id="fake-en", language="en-US", label="English")],
        output_dir=tmp_path / "tts",
    )
    service = CapabilitiesService(translation=translation, tts=tts)
    calls = 0

    def _fake_build(settings: Settings) -> CapabilitiesService:
        nonlocal calls
        del settings
        calls += 1
        return service

    monkeypatch.setattr("app.api.deps.build_capabilities_service", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/capabilities").status_code == 200
        assert client.get("/api/capabilities").status_code == 200
    assert calls == 1
    assert app.state.capabilities_service is service
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/api/test_jobs.py::test_get_job_service_caches_instance_on_app_state backend/tests/api/test_capabilities.py::test_get_capabilities_service_caches_instance_on_app_state -v`
Expected: FAIL with `assert 3 == 1` and `assert 2 == 1`

- [ ] **Step 3: Implement `request.app.state` caching in `backend/app/api/deps.py`**

In `get_job_service(request: Request) -> JobService` and `get_capabilities_service(request: Request) -> CapabilitiesService` in `backend/app/api/deps.py`, assign the newly built service onto `request.app.state.job_service` and `request.app.state.capabilities_service` before returning it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/api/test_jobs.py backend/tests/api/test_capabilities.py -v`
Expected: PASS

---

### Task 2: Move temporary TTS files instead of copying and unlink failed/empty TTS artifacts (Finding #2)

**Files:**
- Modify: `backend/app/application/pipeline/orchestrator.py:5,251-254,371-375`
- Modify: `backend/app/providers/tts/edge.py:72-80`
- Test: `backend/tests/application/test_pipeline.py`, `backend/tests/providers/test_edge.py`

**Interfaces:**
- Consumes: `Path.replace`, `Path.unlink(missing_ok=True)`
- Produces: `_place_artifact(source: Path, destination: Path) -> None` in `backend/app/application/pipeline/orchestrator.py` and `EdgeTTSProvider.synthesize` cleanup in `backend/app/providers/tts/edge.py`

- [ ] **Step 1: Write the failing tests in `backend/tests/application/test_pipeline.py` and `backend/tests/providers/test_edge.py`**

Append to `backend/tests/application/test_pipeline.py`:
```python
def test_tts_scratch_files_are_moved_not_left_behind(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job, max_chars=20)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    scratch_dir = tmp_path / "tts-scratch"
    assert list(scratch_dir.glob("*")) == []
```

In `backend/tests/providers/test_edge.py`, add a test verifying that when the client writes an empty file or raises an error after creating `output_path`, `output_dir` has no leftover `.mp3` files:
```python
def test_edge_synthesize_cleans_up_empty_or_failed_temp_file(tmp_path: Path) -> None:
    class _BrokenClient:
        def list_voices(self) -> list[dict[str, str]]:
            return [{"ShortName": "en-US-AriaNeural", "Locale": "en-US", "FriendlyName": "Aria"}]

        async def synthesize(
            self, text: str, voice: str, rate: str, output_path: Path
        ) -> None:
            del text, voice, rate
            output_path.write_bytes(b"")

    out_dir = tmp_path / "edge-tmp"
    provider = EdgeTTSProvider(out_dir, client=_BrokenClient())
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.synthesize("hello", "en-US", "en-US-AriaNeural", TTSSettings(speed=1.0)))
    assert exc.value.error_type is ErrorType.TTS_FAILED
    assert list(out_dir.glob("*.mp3")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/application/test_pipeline.py::test_tts_scratch_files_are_moved_not_left_behind backend/tests/providers/test_edge.py::test_edge_synthesize_cleans_up_empty_or_failed_temp_file -v`
Expected: FAIL because `tts-scratch` and `edge-tmp` still contain leftover files

- [ ] **Step 3: Implement `source.replace(destination)` and failure cleanup in `orchestrator.py` and `edge.py`**

1. In `backend/app/application/pipeline/orchestrator.py`:
   - Remove `import shutil`.
   - In `_place_artifact(source: Path, destination: Path) -> None`, replace `shutil.copyfile(source, destination)` with `source.replace(destination)`.
   - In `_generate_audio`, if `not _nonempty(destination)`, call `destination.unlink(missing_ok=True)` before raising `DomainError(ErrorType.TTS_FAILED, "tts failed")`.
2. In `backend/app/providers/tts/edge.py`:
   - In `EdgeTTSProvider.synthesize`, call `path.unlink(missing_ok=True)` in the `except` blocks and before raising `DomainError(ErrorType.TTS_FAILED, "tts failed")` when `not path.is_file() or path.stat().st_size == 0`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/application/test_pipeline.py backend/tests/providers/test_edge.py -v`
Expected: PASS

---

### Task 3: Offload blocking Redis, RQ, language detection, and NLLB calls with `asyncio.to_thread` (Finding #3)

**Files:**
- Modify: `backend/app/infrastructure/redis_job_store.py:1-40`
- Modify: `backend/app/infrastructure/rq_queue.py:1-27`
- Modify: `backend/app/providers/language_detection/cpu.py:1-88`
- Modify: `backend/app/providers/translation/nllb.py:1-115`
- Test: `backend/tests/infrastructure/test_job_store.py`, `backend/tests/infrastructure/test_rq_queue.py`, `backend/tests/providers/test_cpu_language_detector.py`, `backend/tests/providers/test_nllb.py`

**Interfaces:**
- Consumes: `asyncio.to_thread`
- Produces: non-blocking `RedisJobCache.{save,get,delete}`, `RQJobQueue.enqueue`, `CpuLanguageDetector.detect`, and `NllbTranslationProvider.translate`

- [ ] **Step 1: Write the failing thread-offload tests**

1. Append to `backend/tests/infrastructure/test_job_store.py`:
```python
def test_redis_job_cache_runs_sync_client_in_worker_thread() -> None:
    import threading
    from app.infrastructure.redis_job_store import RedisJobCache

    main_thread = threading.current_thread()
    seen_threads: list[threading.Thread] = []
    store_data: dict[str, str] = {}

    class _FakeRedis:
        def set(self, key: str, value: str) -> None:
            seen_threads.append(threading.current_thread())
            store_data[key] = value

        def get(self, key: str) -> str | None:
            seen_threads.append(threading.current_thread())
            return store_data.get(key)

        def delete(self, key: str) -> None:
            seen_threads.append(threading.current_thread())
            store_data.pop(key, None)

    cache = RedisJobCache(_FakeRedis())  # type: ignore[arg-type]
    job = _job()
    asyncio.run(cache.save(job))
    assert asyncio.run(cache.get(job.id)) == job
    asyncio.run(cache.delete(job.id))
    assert len(seen_threads) == 3
    assert all(thread is not main_thread for thread in seen_threads)
```

2. Append to `backend/tests/infrastructure/test_rq_queue.py`:
```python
def test_enqueue_runs_in_worker_thread() -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class _ThreadQueue(_CaptureQueue):
        def enqueue(self, *args: object, **kwargs: object) -> None:
            seen.append(threading.current_thread())
            super().enqueue(*args, **kwargs)

    queue = _ThreadQueue()
    asyncio.run(RQJobQueue(queue, job_timeout=1800).enqueue("11111111-1111-1111-1111-111111111111"))  # type: ignore[arg-type]
    assert len(seen) == 1
    assert seen[0] is not main_thread
```

3. Append to `backend/tests/providers/test_cpu_language_detector.py`:
```python
def test_detect_runs_langdetect_in_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class Candidate:
        lang = "en"
        prob = 0.99

    def _fake_detect(text: str) -> list[Candidate]:
        del text
        seen.append(threading.current_thread())
        return [Candidate()]

    monkeypatch.setattr("app.providers.language_detection.cpu.detect_langs", _fake_detect)
    detector = CpuLanguageDetector()
    result = asyncio.run(detector.detect(ENGLISH))
    assert result.language_code == "en-US"
    assert len(seen) == 1
    assert seen[0] is not main_thread
```

4. Append to `backend/tests/providers/test_nllb.py`:
```python
def test_translate_runs_engine_in_worker_thread() -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class _ThreadEngine(FakeNllbEngine):
        def generate(self, text: str, source_flores: str, target_flores: str) -> str:
            seen.append(threading.current_thread())
            return super().generate(text, source_flores, target_flores)

    provider = NllbTranslationProvider(model_id="test-model", engine=_ThreadEngine())
    assert asyncio.run(provider.translate("hello", "en-US", "ja-JP")) == "jpn_Jpan:hello"
    assert len(seen) == 1
    assert seen[0] is not main_thread
```

- [ ] **Step 2: Run the 4 new tests to verify they fail**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/infrastructure/test_job_store.py::test_redis_job_cache_runs_sync_client_in_worker_thread backend/tests/infrastructure/test_rq_queue.py::test_enqueue_runs_in_worker_thread backend/tests/providers/test_cpu_language_detector.py::test_detect_runs_langdetect_in_worker_thread backend/tests/providers/test_nllb.py::test_translate_runs_engine_in_worker_thread -v`
Expected: FAIL because all 4 execute on `main_thread`

- [ ] **Step 3: Wrap blocking calls in `asyncio.to_thread`**

1. In `backend/app/infrastructure/redis_job_store.py`, import `asyncio` and wrap `self._client.set(...)`, `self._client.get(...)`, and `self._client.delete(...)` in `await asyncio.to_thread(...)`.
2. In `backend/app/infrastructure/rq_queue.py`, import `asyncio` and wrap `self._queue.enqueue(...)` in `await asyncio.to_thread(...)`.
3. In `backend/app/providers/language_detection/cpu.py`, import `asyncio`, move the body of `detect` into `def _detect_sync(self, text: str) -> LanguageDetection`, and implement `async def detect(self, text: str) -> LanguageDetection: return await asyncio.to_thread(self._detect_sync, text)`.
4. In `backend/app/providers/translation/nllb.py`, import `asyncio`, move the body of `translate` into `def _translate_sync(self, text: str, source_language: str, target_language: str) -> str`, and implement `async def translate(self, text: str, source_language: str, target_language: str) -> str: return await asyncio.to_thread(self._translate_sync, text, source_language, target_language)`.

- [ ] **Step 4: Run the test suites to verify they pass**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/infrastructure/test_job_store.py backend/tests/infrastructure/test_rq_queue.py backend/tests/providers/test_cpu_language_detector.py backend/tests/providers/test_nllb.py -v`
Expected: PASS

---

### Task 4: Honor `settings.worker_concurrency` in `backend/app/workers/runner.py` (Finding #4)

**Files:**
- Modify: `backend/app/workers/runner.py:1-73`
- Test: `backend/tests/workers/test_process_job.py`

**Interfaces:**
- Consumes: `settings.worker_concurrency: int` from `app.config.settings.Settings`
- Produces: `_run_worker(settings: Settings) -> None` and `_listen(settings: Settings) -> None` in `backend/app/workers/runner.py`

- [ ] **Step 1: Write the failing test in `backend/tests/workers/test_process_job.py`**

Append to `backend/tests/workers/test_process_job.py`:
```python
def test_listen_spawns_extra_processes_when_worker_concurrency_above_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import runner

    events: list[str] = []

    class _FakeProcess:
        def __init__(self, *, target: object, args: tuple[object, ...], daemon: bool) -> None:
            assert target is runner._run_worker
            assert len(args) == 1
            assert daemon is True
            events.append("init")

        def start(self) -> None:
            events.append("start")

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            events.append("terminate")

        def join(self, timeout: float | None = None) -> None:
            del timeout
            events.append("join")

    monkeypatch.setattr("app.workers.runner.Process", _FakeProcess)
    monkeypatch.setattr(
        "app.workers.runner._run_worker",
        lambda settings: events.append(f"work:{settings.worker_concurrency}"),
    )

    settings = Settings(_env_file=None, storage_path=tmp_path, worker_concurrency=3)
    runner._listen(settings)
    assert events == [
        "init",
        "start",
        "init",
        "start",
        "work:3",
        "terminate",
        "join",
        "terminate",
        "join",
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/workers/test_process_job.py::test_listen_spawns_extra_processes_when_worker_concurrency_above_one -v`
Expected: FAIL (`AttributeError: module 'app.workers.runner' has no attribute '_run_worker'`)

- [ ] **Step 3: Implement `_run_worker` and multi-worker process management in `backend/app/workers/runner.py`**

In `backend/app/workers/runner.py`:
- Import `from multiprocessing import Process`.
- Extract `_run_worker(settings: Settings) -> None` containing the `Redis.from_url`, `Queue(...)`, and `Worker(...).work()` call.
- In `_listen(settings: Settings) -> None`:
  ```python
  def _listen(settings: Settings) -> None:
      children: list[Process] = []
      for _ in range(max(0, settings.worker_concurrency - 1)):
          proc = Process(target=_run_worker, args=(settings,), daemon=True)
          proc.start()
          children.append(proc)
      try:
          _run_worker(settings)
      finally:
          for proc in children:
              if proc.is_alive():
                  proc.terminate()
              proc.join(timeout=5)
  ```

- [ ] **Step 4: Run worker tests to verify they pass**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/workers/test_process_job.py -v`
Expected: PASS

---

### Task 5: Deduplicate checkpoint filesystem adapter and cache checkpoint entries in memory (Findings #5 & #9)

**Files:**
- Modify: `backend/app/application/pipeline/checkpoint.py:1-125`
- Modify: `backend/app/application/pipeline/orchestrator.py:45-85`
- Modify: `backend/tests/application/test_pipeline.py:18,160-206,320-350`
- Modify: `backend/tests/workers/test_process_job.py:18,59-67,175,194-203`

**Interfaces:**
- Consumes: `CheckpointStorage` protocol from `app.domain.ports`
- Produces: `CheckpointStore(workspace: Path, *, storage: CheckpointStorage)` in `backend/app/application/pipeline/checkpoint.py` with in-memory `self._entries: list[CheckpointEntry] | None` cache and no `_WorkspaceCheckpointStorage` class

- [ ] **Step 1: Write the failing tests in `backend/tests/application/test_pipeline.py`**

Append to `backend/tests/application/test_pipeline.py`:
```python
def test_checkpoint_store_caches_entries_in_memory_and_preserves_state_on_save_failure(
    tmp_path: Path,
) -> None:
    from app.application.pipeline import checkpoint as checkpoint_mod
    from app.infrastructure.checkpoint_fs import CheckpointFilesystem

    assert not hasattr(checkpoint_mod, "_WorkspaceCheckpointStorage")

    workspace = tmp_path / "job"
    workspace.mkdir()
    real_fs = CheckpointFilesystem(workspace)
    load_calls = 0
    fail_save = False

    class _CountingStorage:
        def load_raw(self) -> list[dict[str, str]]:
            nonlocal load_calls
            load_calls += 1
            return real_fs.load_raw()

        def save_raw(self, entries: list[dict[str, str]]) -> None:
            if fail_save:
                raise DomainError(ErrorType.STORAGE_FAILED, "disk full")
            real_fs.save_raw(entries)

        def relative(self, artifact: Path) -> str:
            return real_fs.relative(artifact)

        def resolve(self, artifact_path: str) -> Path:
            return real_fs.resolve(artifact_path)

    store = CheckpointStore(workspace, storage=_CountingStorage())
    a1 = workspace / "chunks" / "chunk-001.translated.txt"
    a1.parent.mkdir(parents=True)
    a1.write_text("one", encoding="utf-8")
    a2 = workspace / "chunks" / "chunk-002.translated.txt"
    a2.write_text("two", encoding="utf-8")

    assert not store.is_complete("chunk-001", STAGE_TRANSLATED)
    store.record("chunk-001", STAGE_TRANSLATED, a1)
    assert store.is_complete("chunk-001", STAGE_TRANSLATED)
    assert not store.is_complete("chunk-002", STAGE_TRANSLATED)
    assert load_calls == 1

    fail_save = True
    with pytest.raises(DomainError):
        store.record("chunk-002", STAGE_TRANSLATED, a2)
    assert not store.is_complete("chunk-002", STAGE_TRANSLATED)
    assert load_calls == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/application/test_pipeline.py::test_checkpoint_store_caches_entries_in_memory_and_preserves_state_on_save_failure -v`
Expected: FAIL (`assert not hasattr(checkpoint_mod, "_WorkspaceCheckpointStorage")`)

- [ ] **Step 3: Delete `_WorkspaceCheckpointStorage`, cache `_entries` in `CheckpointStore`, and wire `CheckpointFilesystem` in tests**

1. In `backend/app/application/pipeline/checkpoint.py`:
   - Remove `import json`, `from typing import Any`, and the entire `_WorkspaceCheckpointStorage` class.
   - Change `CheckpointStore.__init__` to require `storage: CheckpointStorage`:
     ```python
     class CheckpointStore:
         def __init__(self, workspace: Path, *, storage: CheckpointStorage) -> None:
             del workspace
             self._fs: CheckpointStorage = storage
             self._entries: list[CheckpointEntry] | None = None
     ```
   - In `load(self) -> list[CheckpointEntry]`:
     ```python
     def load(self) -> list[CheckpointEntry]:
         if self._entries is None:
             try:
                 self._entries = [CheckpointEntry.from_dict(item) for item in self._fs.load_raw()]
             except (KeyError, TypeError) as exc:
                 raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
         return list(self._entries)
     ```
   - In `record(self, chunk_id: str, stage: str, artifact: Path) -> None`:
     ```python
     def record(self, chunk_id: str, stage: str, artifact: Path) -> None:
         relative = self._fs.relative(artifact)
         entries = [
             item for item in self.load() if not (item.chunk_id == chunk_id and item.stage == stage)
         ]
         entries.append(CheckpointEntry(chunk_id=chunk_id, stage=stage, artifact_path=relative))
         self._fs.save_raw([entry.to_dict() for entry in entries])
         self._entries = entries
     ```
2. In `backend/app/application/pipeline/orchestrator.py`:
   - Require `checkpoint_storage_factory: Callable[[Path], CheckpointStorage]` in `PipelineOrchestrator.__init__` and call `CheckpointStore(workspace, storage=self._checkpoint_storage_factory(workspace))` in `run`.
3. In `backend/tests/application/test_pipeline.py` and `backend/tests/workers/test_process_job.py`:
   - Import `CheckpointFilesystem` from `app.infrastructure.checkpoint_fs`.
   - Pass `"checkpoint_storage_factory": CheckpointFilesystem` in `_orchestrator(...)` in `test_pipeline.py` and `PipelineOrchestrator(...)` in `test_process_job.py`.
   - Pass `storage=CheckpointFilesystem(workspace)` in the 4 direct `CheckpointStore(workspace, storage=CheckpointFilesystem(workspace))` call sites in `test_pipeline.py` and `test_process_job.py`.

- [ ] **Step 4: Run the pipeline, worker, and domain isolation tests to verify they pass**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/application/test_pipeline.py backend/tests/workers/test_process_job.py backend/tests/domain/test_domain_isolation.py -v`
Expected: PASS

---

### Task 6: Deduplicate validation error formatting, remove unused `app.config.get_settings`, and support `.srt` uploads and subtitle stripping on the backend (Findings #6, #7, #8)

**Files:**
- Modify: `backend/app/api/errors.py:31-57`
- Modify: `backend/app/api/parse.py:10,21,63-64,132-141,178-185`
- Modify: `backend/app/application/pipeline/parse.py:1-13`
- Modify: `backend/app/config/settings.py:1,71-73`
- Modify: `backend/app/config/__init__.py:6,13`
- Modify: `backend/tests/test_settings.py:6,115-119`
- Test: `backend/tests/api/test_jobs.py`, `backend/tests/application/test_pipeline.py`

**Interfaces:**
- Consumes: `validation_message(errors: list[Any]) -> str` from `app.api.errors`
- Produces: `strip_srt(text: str) -> str` and `parse_source(text: str, source_language: str, detector: LanguageDetector) -> tuple[str, str]` in `backend/app/application/pipeline/parse.py`; `.srt` upload support in `backend/app/api/parse.py`

- [ ] **Step 1: Write the failing tests in `backend/tests/api/test_jobs.py` and `backend/tests/application/test_pipeline.py`**

1. Append to `backend/tests/api/test_jobs.py`:
```python
def test_post_srt_upload_is_accepted(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    srt_bytes = (
        b"1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        b"2\n00:00:04,000 --> 00:00:06,000\nSecond line.\n"
    )
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("captions.srt", srt_bytes, "application/x-subrip")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _, _, source, _ = memory
    assert "Hello world." in source.texts[job_id]
```

2. Append to `backend/tests/application/test_pipeline.py`:
```python
def test_pipeline_strips_srt_timecodes_and_tags_before_translation(tmp_path: Path) -> None:
    srt_text = (
        "1\n"
        "00:01:02,000 --> 00:01:05,500\n"
        "<i>Alpha is first.</i>\n\n"
        "2\n"
        "00:01:06,000 --> 00:01:09,000\n"
        "Bravo is second.\n"
    )
    job = _job()
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace, text=srt_text)
    assert result.status is JobStatus.COMPLETED
    translated_inputs = " ".join(call[0] for call in translation.calls)
    assert "00:01:02" not in translated_inputs
    assert "-->" not in translated_inputs
    assert "<i>" not in translated_inputs
    assert "Alpha is first." in translated_inputs
    assert "Bravo is second." in translated_inputs


def test_pipeline_rejects_srt_with_only_timecodes_and_no_dialogue(tmp_path: Path) -> None:
    srt_only_meta = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\n"
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace, text=srt_only_meta)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT
```

3. In `backend/tests/test_settings.py`:
   - Remove `get_settings` from the import on line 6 and remove `test_get_settings_returns_settings` on lines 115–119, replacing it with:
```python
def test_config_module_does_not_export_global_get_settings_singleton() -> None:
    import app.config as config_pkg
    import app.config.settings as settings_mod

    assert not hasattr(settings_mod, "get_settings")
    assert not hasattr(config_pkg, "get_settings")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `backend/.venv/bin/pytest -p no:cacheprovider backend/tests/api/test_jobs.py::test_post_srt_upload_is_accepted backend/tests/application/test_pipeline.py::test_pipeline_strips_srt_timecodes_and_tags_before_translation backend/tests/application/test_pipeline.py::test_pipeline_rejects_srt_with_only_timecodes_and_no_dialogue backend/tests/test_settings.py::test_config_module_does_not_export_global_get_settings_singleton -v`
Expected: FAIL

- [ ] **Step 3: Implement validation deduplication, `get_settings` removal, and `.srt` support**

1. In `backend/app/api/errors.py`:
   - Rename `_validation_message` to `validation_message(errors: list[Any]) -> str`.
2. In `backend/app/api/parse.py`:
   - Import `validation_message` from `app.api.errors`, replace `_first_pydantic_message(exc)` with `validation_message(list(exc.errors()))`, and delete `_first_pydantic_message`.
   - Add `"application/x-subrip"` to `_ALLOWED_FILE_MIME`.
   - In `_assert_safe_txt_filename`, allow `.txt` and `.srt` extensions (`if name in {".", ".."} or not name.lower().endswith((".txt", ".srt")): raise DomainError(ErrorType.INVALID_INPUT, "file must be a .txt or .srt document")`).
3. In `backend/app/application/pipeline/parse.py`:
   - Add SRT detection and stripping matching `frontend/src/lib/jobUi.ts`:
     ```python
     import re

     from app.domain.errors import DomainError, ErrorType
     from app.domain.languages import resolve_source_language
     from app.domain.ports import LanguageDetector

     _SRT_TIMECODE_RE = re.compile(
         r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$",
         re.MULTILINE,
     )
     _SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")
     _SRT_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


     def strip_srt(text: str) -> str:
         if not _SRT_TIMECODE_RE.search(text):
             return text
         blocks = re.split(r"\n{2,}", text)
         cleaned_blocks: list[str] = []
         for block in blocks:
             lines: list[str] = []
             for raw_line in block.split("\n"):
                 line = raw_line.strip()
                 if not line or line.upper() == "WEBVTT":
                     continue
                 if _SRT_INDEX_RE.match(line) or _SRT_TIMECODE_RE.match(line):
                     continue
                 cleaned = _SRT_HTML_TAG_RE.sub("", line).strip()
                 if cleaned:
                     lines.append(cleaned)
             if lines:
                 cleaned_blocks.append(" ".join(lines))
         return "\n\n".join(cleaned_blocks).strip()


     async def parse_source(
         text: str, source_language: str, detector: LanguageDetector
     ) -> tuple[str, str]:
         normalized = strip_srt(text.replace("\r\n", "\n").replace("\r", "\n").strip())
         if not normalized:
             raise DomainError(ErrorType.INVALID_INPUT, "text is required")
         resolved = await resolve_source_language(normalized, source_language, detector)
         return normalized, resolved
     ```
4. In `backend/app/config/settings.py` and `backend/app/config/__init__.py`:
   - Remove `from functools import lru_cache` and `@lru_cache def get_settings() -> Settings` from `backend/app/config/settings.py`, and remove `get_settings` from `backend/app/config/__init__.py`.

- [ ] **Step 4: Run the full backend test suite, linter, formatter, hook selftest, and repo scan**

Run: `backend/.venv/bin/pytest -p no:cacheprovider -m "not integration" backend/tests && backend/.venv/bin/ruff check --no-cache backend && backend/.venv/bin/ruff format --check --no-cache backend && python3 .cursor/hooks/selftest.py && python3 .github/scripts/scan_repo.py`
Expected: All tests pass, `ruff check` and `ruff format --check` report clean, `selftest: ok`, and `scan_repo.py` exits 0.
