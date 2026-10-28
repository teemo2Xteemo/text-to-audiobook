import { afterEach, describe, expect, it, vi } from "vitest";

import { analyze, createJob, detectLanguage, getCapabilities, getJob, previewTts } from "./client";
import { ApiError } from "./types";
import { JOB_ID, capabilitiesFixture, jsonResponse, queuedJob } from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("loads capabilities from GET /api/capabilities", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(capabilitiesFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getCapabilities()).resolves.toEqual(capabilitiesFixture);
    expect(fetchMock).toHaveBeenCalledWith("/api/capabilities");
  });

  it("POSTs JSON job create without a file field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ job_id: JOB_ID, status: "queued" }, 202),
    );
    vi.stubGlobal("fetch", fetchMock);
    await createJob({
      text: "Once upon a time",
      source_language: "en-US",
      target_language: "ja-JP",
      voice: "fake-ja",
      speed: 1,
      output_format: "mp3",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: "Once upon a time",
        source_language: "en-US",
        target_language: "ja-JP",
        speed: 1,
        output_format: "mp3",
        voice: "fake-ja",
      }),
    });
  });

  it("POSTs multipart when a file is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ job_id: JOB_ID, status: "queued" }, 202),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["uploaded"], "story.txt", { type: "text/plain" });
    await createJob({
      file,
      source_language: "en-US",
      target_language: "ja-JP",
      speed: 1,
      output_format: "wav",
    });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("file")).toBe(file);
    expect(form.get("text")).toBeNull();
    expect(form.get("output_format")).toBe("wav");
  });

  it("forwards translation_profile_id in JSON and multipart requests", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse({ job_id: JOB_ID, status: "queued" }, 202)),
    );
    vi.stubGlobal("fetch", fetchMock);

    // JSON
    await createJob({
      text: "hello",
      source_language: "en-US",
      target_language: "vi-VN",
      speed: 1,
      output_format: "mp3",
      translation_profile_id: "gemini-3.5-flash",
    });
    const jsonBody = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(jsonBody.translation_profile_id).toBe("gemini-3.5-flash");

    // Multipart
    const file = new File(["content"], "doc.txt", { type: "text/plain" });
    await createJob({
      file,
      source_language: "en-US",
      target_language: "vi-VN",
      speed: 1,
      output_format: "mp3",
      translation_profile_id: "nllb-600m",
    });
    const form = (fetchMock.mock.calls[1]?.[1] as RequestInit).body as FormData;
    expect(form.get("translation_profile_id")).toBe("nllb-600m");
  });

  it("rejects sending both text and file", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      createJob({
        text: "paste",
        file: new File(["x"], "story.txt", { type: "text/plain" }),
        source_language: "en-US",
        target_language: "ja-JP",
        speed: 1,
        output_format: "mp3",
      }),
    ).rejects.toMatchObject({ error_type: "INVALID_INPUT" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("parses an error envelope without a stack", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error_type: "INVALID_INPUT", message: "target_language is required" }, 400),
      ),
    );
    await expect(getJob(JOB_ID)).rejects.toEqual(
      expect.objectContaining({
        error_type: "INVALID_INPUT",
        message: "target_language is required",
      }),
    );
    await expect(getJob(JOB_ID)).rejects.not.toBeInstanceOf(TypeError);
    await expect(getJob(JOB_ID)).rejects.toBeInstanceOf(ApiError);
  });

  it("extracts FastAPI detail string as error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Not found" }, 404),
      ),
    );
    await expect(getJob(JOB_ID)).rejects.toEqual(
      expect.objectContaining({
        error_type: "INVALID_INPUT",
        message: "Not found",
        status: 404,
      }),
    );
  });

  it("handles non-JSON 500 error without masking as INVALID_INPUT", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Internal Server Error", {
          status: 500,
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );
    await expect(getJob(JOB_ID)).rejects.toEqual(
      expect.objectContaining({
        error_type: "INTERNAL_ERROR",
        message: "Internal Server Error",
        status: 500,
      }),
    );
  });

  it("returns job status for polling", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(queuedJob())));
    await expect(getJob(JOB_ID)).resolves.toMatchObject({
      status: "queued",
      stage: "queued",
      chunk_current: 0,
    });
  });

  it("POSTs detect-language with text payload", async () => {
    const fakeDetect = {
      language: "en-US",
      confidence: 0.99,
      needs_confirmation: false,
      candidates: [{ language: "en-US", confidence: 0.99 }],
      preview_lines: ["Hello world"],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fakeDetect));
    vi.stubGlobal("fetch", fetchMock);

    const result = await detectLanguage("Hello world");
    expect(result).toEqual(fakeDetect);
    expect(fetchMock).toHaveBeenCalledWith("/api/detect-language", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Hello world" }),
    });
  });

  it("POSTs analyze with JSON for text input", async () => {
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "en-US",
      confidence: 0.95,
      needs_confirmation: false,
      candidates: [{ language: "en-US", confidence: 0.95 }],
      chapters_count: 1,
      chapters_preview: [],
      duration_estimate_seconds: 10,
      process_time_estimate_seconds: 1.5,
      preview_lines: ["Story start"],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fakeAnalyze));
    vi.stubGlobal("fetch", fetchMock);

    const result = await analyze({ text: "Story start" });
    expect(result).toEqual(fakeAnalyze);
    expect(fetchMock).toHaveBeenCalledWith("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Story start" }),
    });
  });

  it("POSTs analyze with FormData for file input", async () => {
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "en-US",
      confidence: 0.9,
      needs_confirmation: false,
      candidates: [],
      chapters_count: 1,
      chapters_preview: [],
      duration_estimate_seconds: 20,
      process_time_estimate_seconds: 3,
      preview_lines: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fakeAnalyze));
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["file content"], "chapter1.txt", { type: "text/plain" });
    const result = await analyze({ file });
    expect(result).toEqual(fakeAnalyze);
    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe("/api/analyze");
    expect(call?.[1]?.method).toBe("POST");
    expect(call?.[1]?.body).toBeInstanceOf(FormData);
    expect((call?.[1]?.body as FormData).get("file")).toBe(file);
  });

  it("POSTs tts preview with payload", async () => {
    const fakePreview = { audio_base64: "dGVzdA==", mime_type: "audio/mpeg" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fakePreview));
    vi.stubGlobal("fetch", fetchMock);

    const result = await previewTts({ text: "Hello", voice: "fake-en", speed: 1.25 });
    expect(result).toEqual(fakePreview);
    expect(fetchMock).toHaveBeenCalledWith("/api/tts/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Hello", voice: "fake-en", speed: 1.25 }),
    });
  });

  it("forwards mode in createJob for both JSON and multipart", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse({ job_id: JOB_ID, status: "queued" }, 202)),
    );
    vi.stubGlobal("fetch", fetchMock);

    // JSON with narrate mode
    await createJob({
      text: "narrate text",
      source_language: "en-US",
      target_language: "en-US",
      speed: 1,
      output_format: "mp3",
      mode: "narrate",
    });
    const jsonBody = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(jsonBody.mode).toBe("narrate");

    // Multipart with narrate mode
    const file = new File(["content"], "narrate.txt", { type: "text/plain" });
    await createJob({
      file,
      source_language: "en-US",
      target_language: "en-US",
      speed: 1,
      output_format: "wav",
      mode: "narrate",
    });
    const form = (fetchMock.mock.calls[1]?.[1] as RequestInit).body as FormData;
    expect(form.get("mode")).toBe("narrate");
  });
});
