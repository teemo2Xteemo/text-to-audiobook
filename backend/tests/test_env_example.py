import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Settings + Vite names already used by Compose. HF_HOME is Compose-only (comment).
REQUIRED_ENV_NAMES = (
    "REDIS_URL",
    "STORAGE_PATH",
    "OUTPUT_BITRATE_KBPS",
    "MAX_UPLOAD_BYTES",
    "TRANSLATION_PROVIDER",
    "TTS_PROVIDER",
    "TTS_DEFAULT_VOICE_BY_LANGUAGE",
    "WORKER_CONCURRENCY",
    "NLLB_MODEL_ID",
    "OLLAMA_BASE_URL",
    "OLLAMA_TRANSLATION_MODEL",
    "OLLAMA_HTTP_TIMEOUT_SECONDS",
    "LANGUAGE_DETECT_MIN_CONFIDENCE",
    "LANGUAGE_CONFIRM_THRESHOLD",
    "TTS_PREVIEW_RATE_PER_MINUTE",
    "ESTIMATE_CHARS_PER_SECOND",
    "ESTIMATE_TRANSLATION_EXPANSION",
    "NARRATE_CHAPTER_PAUSE_MS",
    "TEXT_NORMALIZER_PROVIDER",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "MAX_RECOVERY_ATTEMPTS",
    "RQ_JOB_TIMEOUT_SECONDS",
    "JOB_RUN_BUDGET_SECONDS",
    "MAX_STALLED_RUNS",
    "MAX_JOB_RUNS",
    "VITE_API_BASE_URL",
    "VITE_DEFAULT_SOURCE_LANGUAGE",
    "VITE_DEFAULT_TARGET_LANGUAGE",
)

WORKER_REDIS_PROBE = "import os, redis; redis.Redis.from_url(os.environ['REDIS_URL']).ping()"


def test_env_example_lists_locked_names() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in REQUIRED_ENV_NAMES:
        assert name in text, name
    assert "HF_HOME" in text
    assert "CACHE_PATH" not in text
    assert "TTS_PREVIEW_RATE_PER_MINUTE=30" in text


def _compose_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_compose_is_cpu_only_with_worker_redis_healthcheck() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    body = _compose_without_comments(text)
    assert "runtime: nvidia" not in body
    assert "gpus:" not in body
    assert WORKER_REDIS_PROBE in text
    assert "interval: 10s" in text
    assert "OLLAMA_BASE_URL" in text
    assert "OLLAMA_TRANSLATION_MODEL" in text
    assert "OLLAMA_HTTP_TIMEOUT_SECONDS" in text
    assert body.count("OLLAMA_BASE_URL: ${OLLAMA_BASE_URL") == 2
    assert body.count("OLLAMA_TRANSLATION_MODEL: ${OLLAMA_TRANSLATION_MODEL") == 2
    assert body.count("OLLAMA_HTTP_TIMEOUT_SECONDS: ${OLLAMA_HTTP_TIMEOUT_SECONDS") == 2
    assert body.count("host.docker.internal:host-gateway") == 2
    assert body.count("OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}") == 2


def _assignment_value(text: str, name: str) -> str:
    prefix = f"{name}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix).strip()
    raise AssertionError(f"{name} is missing from .env.example")


def _nginx_client_max_body_bytes(text: str) -> int:
    match = re.search(r"client_max_body_size\s+(\d+)([kmgKMG])?;", text)
    assert match is not None, text
    value = int(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "k":
        return value * 1024
    if unit == "m":
        return value * 1024 * 1024
    if unit == "g":
        return value * 1024 * 1024 * 1024
    return value


def test_max_upload_bytes_defaults_match_settings() -> None:
    from app.config.settings import Settings

    expected = str(Settings.model_fields["max_upload_bytes"].default)
    assert expected == "52428800"
    env_value = _assignment_value(ENV_EXAMPLE.read_text(encoding="utf-8"), "MAX_UPLOAD_BYTES")
    assert env_value == expected
    compose = _compose_without_comments(COMPOSE.read_text(encoding="utf-8"))
    assert f"MAX_UPLOAD_BYTES: ${{MAX_UPLOAD_BYTES:-{expected}}}" in compose
    nginx = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    nginx_limit = _nginx_client_max_body_bytes(nginx)
    assert nginx_limit >= int(expected)
    assert "client_max_body_size 0" not in nginx


def test_compose_worker_wires_max_recovery_attempts() -> None:
    body = _compose_without_comments(COMPOSE.read_text(encoding="utf-8"))
    assert body.count("MAX_RECOVERY_ATTEMPTS: ${MAX_RECOVERY_ATTEMPTS:-3}") == 1
    assert body.count("JOB_RUN_BUDGET_SECONDS: ${JOB_RUN_BUDGET_SECONDS:-}") == 1
    assert body.count("MAX_STALLED_RUNS: ${MAX_STALLED_RUNS:-3}") == 1
    assert body.count("MAX_JOB_RUNS: ${MAX_JOB_RUNS:-500}") == 1
