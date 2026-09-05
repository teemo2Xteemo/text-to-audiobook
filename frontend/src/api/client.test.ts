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

  it("returns job status for polling", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(queuedJob())));
    await expect(getJob(JOB_ID)).resolves.toMatchObject({
      status: "queued",
      stage: "queued",
      chunk_current: 0,
    });
  });
});
