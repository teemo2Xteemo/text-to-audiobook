export type JobStatus =
  | "queued"
  | "parsing"
  | "translating"
  | "preparing_tts"
  | "generating_audio"
  | "merging"
  | "completed"
  | "failed";

export type OutputFormat = "mp3" | "wav";

export type Voice = {
  id: string;
  language: string;
  label: string;
};

export type Capabilities = {
  languages: string[];
  voices: Voice[];
};

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
