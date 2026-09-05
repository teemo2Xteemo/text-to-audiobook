import { apiBaseUrl } from "../lib/env";
import { ApiError, type CreateJobInput, type Capabilities, type JobCreated, type JobStatusResponse } from "./types";

function requestUrl(path: string): string {
  const base = apiBaseUrl().replace(/\/$/, "");
  return `${base}${path}`;
}

function isEnvelope(value: unknown): value is { error_type: string; message: string } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as { error_type?: unknown; message?: unknown };
  return typeof record.error_type === "string" && typeof record.message === "string";
}

async function parseError(response: Response): Promise<never> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError("INVALID_INPUT", "request failed", response.status);
  }
  if (isEnvelope(body)) {
    throw new ApiError(body.error_type, body.message, response.status);
  }
  throw new ApiError("INVALID_INPUT", "request failed", response.status);
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

export async function getJob(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(requestUrl(`/api/jobs/${jobId}`));
  return parseJson<JobStatusResponse>(response);
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
    form.set("file", input.file);
    const response = await fetch(requestUrl("/api/jobs"), {
      method: "POST",
      body: form,
    });
    return parseJson<JobCreated>(response);
  }

  const body: Record<string, string | number> = {
    text: input.text ?? "",
    source_language: input.source_language,
    target_language: input.target_language,
    speed: input.speed,
    output_format: input.output_format,
  };
  if (input.voice) {
    body.voice = input.voice;
  }
  const response = await fetch(requestUrl("/api/jobs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson<JobCreated>(response);
}
