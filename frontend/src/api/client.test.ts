import { afterEach, describe, expect, it, vi } from "vitest";

import { createJob, getCapabilities, getJob } from "./client";
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
        error_type: "STORAGE_FAILED",
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
});
