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
    "LANGUAGE_DETECT_MIN_CONFIDENCE",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "RQ_JOB_TIMEOUT_SECONDS",
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


def _compose_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_compose_is_cpu_only_with_worker_redis_healthcheck() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    body = _compose_without_comments(text)
    assert "runtime: nvidia" not in body
    assert "gpus:" not in body
    assert WORKER_REDIS_PROBE in text
    assert "interval: 10s" in text
