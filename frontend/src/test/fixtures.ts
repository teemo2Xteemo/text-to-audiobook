import type { Capabilities, JobStatusResponse } from "../api/types";

export const JOB_ID = "11111111-1111-4111-8111-111111111111";

export const capabilitiesFixture: Capabilities = {
  languages: ["en-US", "ja-JP"],
  voices: [
    { id: "fake-en", language: "en-US", label: "English A" },
    { id: "fake-ja", language: "ja-JP", label: "Japanese A" },
  ],
};

export function queuedJob(overrides: Partial<JobStatusResponse> = {}): JobStatusResponse {
  return {
    job_id: JOB_ID,
    status: "queued",
    stage: "queued",
    chunk_current: 0,
    chunk_total: 0,
    error_type: null,
    message: null,
    source_language: "en-US",
    target_language: "ja-JP",
    voice: "fake-ja",
    speed: 1.0,
    output_format: "mp3",
    audio_url: null,
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
