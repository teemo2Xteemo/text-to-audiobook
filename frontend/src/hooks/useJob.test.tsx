import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JOB_ID, jsonResponse, queuedJob } from "../test/fixtures";
import { useJob } from "./useJob";

function createdJob(): Response {
  return jsonResponse({ job_id: JOB_ID, status: "queued" }, 202);
}

function submitInput() {
  return {
    text: "hello",
    source_language: "en-US",
    target_language: "ja-JP",
    speed: 1,
    output_format: "mp3" as const,
  };
}

describe("useJob poll retry", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  async function submitAndPoll(responses: Response[]) {
    const queue = [...responses];
    const fetchMock = vi.fn(async () => {
      const next = queue.shift();
      if (!next) {
        throw new Error("unexpected fetch");
      }
      return next;
    });
    vi.stubGlobal("fetch", fetchMock);
    const hook = renderHook(() => useJob());
    await act(async () => {
      await hook.result.current.submit(submitInput());
    });
    return { hook, fetchMock };
  }

  it("fails immediately on 404 and does not keep polling", async () => {
    const { hook, fetchMock } = await submitAndPoll([
      createdJob(),
      jsonResponse(queuedJob()),
      jsonResponse({ error_type: "INVALID_INPUT", message: "job not found" }, 404),
    ]);
    expect(hook.result.current.status).toBe("loading");
    const callsAfterSubmit = fetchMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(hook.result.current.status).toBe("error");
    if (hook.result.current.status === "error") {
      expect(hook.result.current.error.status).toBe(404);
      expect(hook.result.current.error.error_type).toBe("INVALID_INPUT");
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock.mock.calls.length).toBe(callsAfterSubmit + 1);
  });

  it("errors on the third consecutive 500 and resets the counter after success", async () => {
    const { hook } = await submitAndPoll([
      createdJob(),
      jsonResponse(queuedJob()),
      jsonResponse({ error_type: "INTERNAL_ERROR", message: "down" }, 500),
      jsonResponse({ error_type: "INTERNAL_ERROR", message: "down" }, 500),
      jsonResponse(queuedJob({ status: "translating", stage: "translating" })),
      jsonResponse({ error_type: "INTERNAL_ERROR", message: "down" }, 500),
      jsonResponse({ error_type: "INTERNAL_ERROR", message: "down" }, 500),
      jsonResponse({ error_type: "INTERNAL_ERROR", message: "down" }, 500),
    ]);

    for (let attempt = 0; attempt < 4; attempt += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(hook.result.current.status).toBe("loading");
    }

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("loading");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("error");
    if (hook.result.current.status === "error") {
      expect(hook.result.current.error.status).toBe(500);
    }
  });

  it("retries a 429 poll and keeps the job running", async () => {
    const { hook } = await submitAndPoll([
      createdJob(),
      jsonResponse(queuedJob()),
      jsonResponse({ error_type: "PROVIDER_RATE_LIMIT", message: "slow down" }, 429),
      jsonResponse(queuedJob({ status: "translating", stage: "translating" })),
    ]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("loading");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("loading");
    if (hook.result.current.status === "loading") {
      expect(hook.result.current.job?.status).toBe("translating");
    }
  });

  it("retries a network failure and keeps polling after it recovers", async () => {
    const queue: Array<Response | Error> = [
      createdJob(),
      jsonResponse(queuedJob()),
      new TypeError("network down"),
      jsonResponse(queuedJob({ status: "translating", stage: "translating" })),
    ];
    const fetchMock = vi.fn(async () => {
      const next = queue.shift();
      if (next instanceof Error) {
        throw next;
      }
      if (!next) {
        throw new Error("unexpected fetch");
      }
      return next;
    });
    vi.stubGlobal("fetch", fetchMock);
    const hook = renderHook(() => useJob());
    await act(async () => {
      await hook.result.current.submit(submitInput());
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("loading");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(hook.result.current.status).toBe("loading");
    if (hook.result.current.status === "loading") {
      expect(hook.result.current.job?.status).toBe("translating");
    }
  });
});
