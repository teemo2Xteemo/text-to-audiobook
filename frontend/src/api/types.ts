export type JobStatus =
  | "queued"
  | "parsing"
  | "translating"
  | "preparing_tts"
  | "generating_audio"
  | "merging"
  | "completed"
  | "failed";

export type JobMode = "translate" | "narrate" | "subtitle";

export type DocumentFormat = "txt" | "srt" | "vtt" | "ass";

export type OutputFormat = "mp3" | "wav";

export type SrtTiming = "continuous" | "aligned";

export type Voice = {
  id: string;
  language: string;
  label: string;
  engine?: string;
};

export type TtsEngine = {
  id: string;
  label: string;
  kind: "online" | "local";
  cost_label: string;
  available: boolean;
  languages: string[];
  max_concurrency: number;
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

export type UploadLimits = {
  max_upload_bytes: number;
};

export type Capabilities = {
  languages: string[];
  voices: Voice[];
  tts_engines?: TtsEngine[];
  translation_profiles?: TranslationProfile[];
  narrate_languages?: string[];
  estimate_defaults?: { chars_per_second?: number; preview_wpm?: number };
  text_normalization?: boolean;
  limits?: UploadLimits;
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
  srt_timing?: SrtTiming;
  normalize_text?: boolean;
  run_count?: number;
  chapters_total?: number;
  chapters_ready?: number;
  chapters_failed?: number;
  chapters_url?: string | null;
  zip_url?: string | null;
  tts_engine?: string | null;
  continue_with_edge?: boolean;
  tts_mix_warning?: string | null;
  document_url?: string | null;
  document_format?: DocumentFormat | null;
  line_total?: number;
};

export type ChapterAudioStatus = "pending" | "generating" | "ready" | "failed";

export type ChapterListItem = {
  ordinal: number;
  index: number;
  kind: ChapterKind;
  chapter_number?: number | null;
  title: string;
  title_source: string;
  status: ChapterAudioStatus;
  chunk_first: number;
  chunk_last: number;
  duration_ms?: number | null;
  size_bytes?: number | null;
  error_type?: string | null;
  message?: string | null;
  filename: string;
  download_url?: string | null;
  tts_engine?: string | null;
  note?: string | null;
};

export type ChapterListResponse = {
  job_id: string;
  total: number;
  ready: number;
  failed: number;
  matched: number;
  offset: number;
  limit: number;
  partial: boolean;
  items: ChapterListItem[];
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
  srt_timing?: SrtTiming;
  normalize_text?: boolean;
  ttsEngine?: string;
  document_format?: DocumentFormat;
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

export type ChapterKind = "preface" | "volume" | "chapter";

export type ChapterSummary = {
  index: number;
  title: string;
  char_count: number;
  /** Unique 1-based span id. Equal to `index`. #29 file names should use this. */
  ordinal?: number;
  kind?: ChapterKind;
  /** Printed chapter number. Null for preface and volume. May restart per volume. */
  chapter_number?: number | null;
};

export type AnalyzeResponse = {
  encoding: string;
  language: string;
  confidence: number;
  needs_confirmation: boolean;
  candidates: LanguageCandidate[];
  chapters_count: number;
  /** Every detected span, including ones past `chapters_preview`. */
  chapters_span_count?: number;
  chapters_preview: ChapterSummary[];
  chapters_preview_truncated?: boolean;
  chapter_detection_confidence?: number;
  chapter_detection_uncertain?: boolean;
  duration_estimate_seconds: number;
  process_time_estimate_seconds: number;
  preview_lines: string[];
};

export type TTSPreviewPayload = {
  text: string;
  voice: string;
  speed?: number;
  tts_engine?: string;
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
