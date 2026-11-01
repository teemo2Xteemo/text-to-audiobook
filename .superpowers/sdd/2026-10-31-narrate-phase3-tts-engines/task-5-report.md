# Task 5 report (transcribed by controller from the implementer's final message; implementer did not write this file)

Status: DONE — commit a61d898 feat: per-job TTS engine pin (#28)
Tests: 798 passed, 3 skipped; ruff check + format --check clean. mypy not installed (project lint does not use mypy).

## Implemented (13 files)
- Job.tts_engine (to_dict only when set → old status.json unchanged); CreateJobCommand.tts_engine; JSON body, multipart form, JobStatusResponse carry tts_engine; TTS_ENGINES_ENABLED in settings + .env.example.
- factory.py: enabled_tts_engines (default first, lowercase, dedupe); build_tts_engines (None on ProviderConfigurationError); settings_for_tts_pin; build_job_service passes tts_engines + default_tts_engine.
- JobService.create: lowercases pin, INVALID_INPUT if not enabled, PROVIDER_CONFIGURATION_ERROR if provider None (placeholder msg for Task 6); narrate resolves voice against pinned provider (default when unpinned) — RF2.
- runner.py: orchestrator built from settings_for_translation_pin(settings_for_tts_pin(settings, fresh), fresh).

## Tests added
- job record round-trip + legacy payload without tts_engine
- create: pin resolves voice; unknown engine INVALID_INPUT; fish:* voice with no pin / pin edge → UNSUPPORTED_LANGUAGE; unbuilt engine → PROVIDER_CONFIGURATION_ERROR
- API: JSON and multipart carry same tts_engine; unknown engine → 400
- factory: engine list parsing, build_tts_engines, pin rewrite
- worker RF3: fish-pinned narrate job, Fish raises PROVIDER_RATE_LIMIT after 2 calls → FAILED/PROVIDER_RATE_LIMIT, fish called exactly twice, checkpoint.json exists, spy on EdgeTTSProvider.synthesize zero calls; no-op sleep.

## Concerns (implementer's)
- No RED run captured: implementation written before tests.
- Translate mode validates + stores pin but create does not resolve voice there (never did; orchestrator resolves later).
- Worker test sets orchestrator._tts/_audio/_sleep directly (real factory would use ffmpeg for fish, which can't process fake bytes).
- JobService gained extra param default_tts_engine: str = "fake" (not in brief) naming the fallback {default: tts} map.

## Fix round 1
- jobs.py: translate branch now calls resolve_voice(tts, target_language, voice) when a voice and provider exist (RF2 -> 400 UNSUPPORTED_LANGUAGE at create). Pin normalized via `(command.tts_engine or "").strip().lower() or None` (blank = unpinned).
- Tests added: translate-mode cross-engine voice (pin None/edge) unit + API 400; translate voice resolves on pinned fish; blank pin -> unpinned.
- Worker test: stub only substituted when effective tts_provider == "fish". Reverting `settings_for_tts_pin` in runner.py made it fail: `AssertionError: assert 'edge' == 'fish'`; line restored.
- Run: `backend/.venv/bin/pytest backend/tests -q` -> 804 passed, 3 skipped; ruff check/format clean.
