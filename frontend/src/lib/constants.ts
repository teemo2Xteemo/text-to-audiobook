export const AUTO_SOURCE_LANGUAGE = "auto";
export const SPEED_MIN = 0.5;
export const SPEED_MAX = 2.0;
export const SPEED_DEFAULT = 1.0;
export const POLL_INTERVAL_MS = 1000;
export const LAST_JOB_STORAGE_KEY = "last_job_id";
export const TTS_ENGINE_STORAGE_KEY = "audiobook_studio_tts_engine";
export const THEME_STORAGE_KEY = "audiobook_studio_theme";
// Fallback only, used when capabilities.limits.max_upload_bytes is missing.
// MAX_UPLOAD_BYTES on the API is the source of truth (default 50 MiB).
export const MAX_FILE_SIZE_BYTES = 52_428_800;
export const ALLOWED_FILE_EXTENSIONS = [".txt", ".srt", ".vtt", ".ass"] as const;

