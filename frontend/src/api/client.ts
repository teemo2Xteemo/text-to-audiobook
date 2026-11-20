import { apiBaseUrl } from "../lib/env";
import {
  ApiError,
  type AnalyzeResponse,
  type Capabilities,
  type CreateJobInput,
  type DetectLanguageResponse,
  type ChapterListResponse,
  type JobCreated,
  type JobStatusResponse,
  type TTSPreviewPayload,
  type TTSPreviewResponse,
} from "./types";

function requestUrl(path: string): string {
  const base = apiBaseUrl().replace(/\/$/, "");
  return `${base}${path}`;
}

const PAYLOAD_TOO_LARGE_MESSAGE =
  "Upload is too large for the server limit. Try a smaller file or raise MAX_UPLOAD_BYTES.";

function isEnvelope(value: unknown): value is { error_type: string; message: string } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as { error_type?: unknown; message?: unknown };
  return typeof record.error_type === "string" && typeof record.message === "string";
}

function extractDetailMessage(body: unknown): string | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const record = body as { detail?: unknown };
  if (typeof record.detail === "string") {
    return record.detail;
  }
  if (Array.isArray(record.detail)) {
    const messages = record.detail
      .map((item) => {
        if (typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string") {
          return item.msg;
        }
        return typeof item === "string" ? item : "";
      })
      .filter(Boolean);
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  return null;
}

function nonEnvelopeMessage(status: number, rawText: string): { errorType: string; message: string } {
  if (status === 413) {
    return { errorType: "PAYLOAD_TOO_LARGE", message: PAYLOAD_TOO_LARGE_MESSAGE };
  }
  const trimmed = rawText.trim();
  if (trimmed.length > 0 && trimmed.length < 200 && !trimmed.startsWith("<")) {
    return {
      errorType: status >= 500 ? "INTERNAL_ERROR" : "INVALID_INPUT",
      message: trimmed,
    };
  }
  if (status >= 500) {
    return { errorType: "INTERNAL_ERROR", message: `server error (${status})` };
  }
  return { errorType: "INVALID_INPUT", message: "request failed" };
}

async function parseError(response: Response): Promise<never> {
  const defaultErrorType = response.status >= 500 ? "INTERNAL_ERROR" : "INVALID_INPUT";
  let body: unknown = null;
  let rawText = "";

  try {
    rawText = await response.text();
    body = JSON.parse(rawText);
  } catch {
    const parsed = nonEnvelopeMessage(response.status, rawText);
    throw new ApiError(parsed.errorType, parsed.message, response.status);
  }

  if (isEnvelope(body)) {
    throw new ApiError(body.error_type, body.message, response.status);
  }

  if (response.status === 413) {
    throw new ApiError("PAYLOAD_TOO_LARGE", PAYLOAD_TOO_LARGE_MESSAGE, response.status);
  }

  const detailMsg = extractDetailMessage(body);
  if (detailMsg) {
    throw new ApiError(defaultErrorType, detailMsg, response.status);
  }

  throw new ApiError(
    defaultErrorType,
    response.status >= 500 ? `server error (${response.status})` : "request failed",
    response.status,
  );
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    await parseError(response);
  }
  return (await response.json()) as T;
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof Error) {
    return new ApiError("TIMEOUT", error.message, 0);
  }
  return new ApiError("TIMEOUT", "request failed", 0);
}

export async function getCapabilities(): Promise<Capabilities> {
  const response = await fetch(requestUrl("/api/capabilities"));
  return parseJson<Capabilities>(response);
}

export async function continueWithEdge(jobId: string): Promise<JobCreated> {
  const response = await fetch(requestUrl(`/api/jobs/${jobId}/continue-with-edge`), {
    method: "POST",
  });
  return parseJson<JobCreated>(response);
}

export async function getJob(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(requestUrl(`/api/jobs/${jobId}`));
  return parseJson<JobStatusResponse>(response);
}

export async function getChapters(
  jobId: string,
  query: { offset?: number; limit?: number; status?: string; q?: string } = {},
): Promise<ChapterListResponse> {
  const params = new URLSearchParams();
  if (query.offset !== undefined) {
    params.set("offset", String(query.offset));
  }
  if (query.limit !== undefined) {
    params.set("limit", String(query.limit));
  }
  if (query.status) {
    params.set("status", query.status);
  }
  if (query.q) {
    params.set("q", query.q);
  }
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  const response = await fetch(requestUrl(`/api/jobs/${jobId}/chapters${suffix}`));
  return parseJson<ChapterListResponse>(response);
}

export async function createJob(input: CreateJobInput): Promise<JobCreated> {
  const hasText = Boolean(input.text?.trim());
  const hasFile = Boolean(input.file);
  if (hasText === hasFile) {
    throw new ApiError("INVALID_INPUT", "provide exactly one of text or file", 400);
  }

  if (input.file) {
    const form = new FormData();
    form.set("source_language", input.source_language);
    form.set("target_language", input.target_language);
    form.set("speed", String(input.speed));
    form.set("output_format", input.output_format);
    if (input.voice) {
      form.set("voice", input.voice);
    }
    if (input.translation_profile_id) {
      form.set("translation_profile_id", input.translation_profile_id);
    }
    if (input.mode) {
      form.set("mode", input.mode);
    }
    if (input.srt_timing) {
      form.set("srt_timing", input.srt_timing);
    }
    if (input.normalize_text) {
      form.set("normalize_text", "true");
    }
    if (input.ttsEngine) {
      form.set("tts_engine", input.ttsEngine);
    }
    if (input.document_format) {
      form.set("document_format", input.document_format);
    }
    form.set("file", input.file);
    const response = await fetch(requestUrl("/api/jobs"), {
      method: "POST",
      body: form,
    });
    return parseJson<JobCreated>(response);
  }

  const body: Record<string, string | number | boolean> = {
    text: input.text ?? "",
    source_language: input.source_language,
    target_language: input.target_language,
    speed: input.speed,
    output_format: input.output_format,
  };
  if (input.voice) {
    body.voice = input.voice;
  }
  if (input.translation_profile_id) {
    body.translation_profile_id = input.translation_profile_id;
  }
  if (input.mode) {
    body.mode = input.mode;
  }
  if (input.srt_timing) {
    body.srt_timing = input.srt_timing;
  }
  if (input.normalize_text) {
    body.normalize_text = true;
  }
  if (input.ttsEngine) {
    body.tts_engine = input.ttsEngine;
  }
  if (input.document_format) {
    body.document_format = input.document_format;
  }
  const response = await fetch(requestUrl("/api/jobs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson<JobCreated>(response);
}

export async function detectLanguage(text: string): Promise<DetectLanguageResponse> {
  const response = await fetch(requestUrl("/api/detect-language"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return parseJson<DetectLanguageResponse>(response);
}

export async function analyze(input: { text?: string; file?: File }): Promise<AnalyzeResponse> {
  if (input.file) {
    const form = new FormData();
    form.set("file", input.file);
    const response = await fetch(requestUrl("/api/analyze"), {
      method: "POST",
      body: form,
    });
    return parseJson<AnalyzeResponse>(response);
  }
  const response = await fetch(requestUrl("/api/analyze"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: input.text ?? "" }),
  });
  return parseJson<AnalyzeResponse>(response);
}

export async function previewTts(payload: TTSPreviewPayload): Promise<TTSPreviewResponse> {
  const response = await fetch(requestUrl("/api/tts/preview"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson<TTSPreviewResponse>(response);
}
