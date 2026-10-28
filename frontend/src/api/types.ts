export type JobStatus =
  | "queued"
  | "parsing"
  | "translating"
  | "preparing_tts"
  | "generating_audio"
  | "merging"
  | "completed"
  | "failed";

export type JobMode = "translate" | "narrate";

export type OutputFormat = "mp3" | "wav";

export type Voice = {
  id: string;
  language: string;
  label: string;
};

export type TranslationProfile = {
  id: string;
  provider: string;
  model: string;
  label: string;
  badge: string;
  description: string;
  is_default: boolean;
  supported_languages?: string[];
};

export type Capabilities = {
  languages: string[];
  voices: Voice[];
  translation_profiles?: TranslationProfile[];
  narrate_languages?: string[];
  estimate_defaults?: { chars_per_second?: number; preview_wpm?: number };
};

export type CapabilitiesResponse = Capabilities;

export type JobCreated = {
  job_id: string;
  status: JobStatus;
};

export type JobStatusResponse = {
  job_id: string;
  status: JobStatus;
  stage: JobStatus;
  chunk_current: number;
  chunk_total: number;
  error_type: string | null;
  message: string | null;
  source_language: string;
  target_language: string;
  voice: string | null;
  speed: number;
  output_format: OutputFormat;
  audio_url: string | null;
  subtitles_url?: string | null;
  translation_provider?: string | null;
  translation_model?: string | null;
  mode?: JobMode;
};

export type ErrorEnvelope = {
  error_type: string;
  message: string;
};

export type CreateJobInput = {
  source_language: string;
  target_language: string;
  voice?: string;
  speed: number;
  output_format: OutputFormat;
  text?: string;
  file?: File;
  translation_profile_id?: string;
  mode?: JobMode;
};

export type CreateJobPayload = CreateJobInput;

export type LanguageCandidate = {
  language: string;
  confidence: number;
};

export type DetectLanguageResponse = {
  language: string;
  confidence: number;
  needs_confirmation: boolean;
  candidates: LanguageCandidate[];
  preview_lines: string[];
};

export type ChapterSummary = {
  index: number;
  title: string;
  char_count: number;
};

export type AnalyzeResponse = {
  encoding: string;
  language: string;
  confidence: number;
  needs_confirmation: boolean;
  candidates: LanguageCandidate[];
  chapters_count: number;
  chapters_preview: ChapterSummary[];
  duration_estimate_seconds: number;
  process_time_estimate_seconds: number;
  preview_lines: string[];
};

export type TTSPreviewPayload = {
  text: string;
  voice: string;
  speed?: number;
};

export type TTSPreviewResponse = {
  audio_base64: string;
  mime_type: string;
};

export class ApiError extends Error {
  readonly error_type: string;
  readonly status: number;

  constructor(error_type: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.error_type = error_type;
    this.status = status;
  }
}
