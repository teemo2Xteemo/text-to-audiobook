import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { JOB_ID, capabilitiesFixture, jsonResponse, queuedJob } from "./test/fixtures";
import { LAST_JOB_STORAGE_KEY } from "./lib/constants";

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return handler(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("App", () => {
  it("creates a job from paste, polls, and plays completed audio", async () => {
    const user = userEvent.setup();
    let polls = 0;
    mockFetch((url, init) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      if (url.endsWith("/api/jobs") && init?.method === "POST") {
        return jsonResponse({ job_id: JOB_ID, status: "queued" }, 202);
      }
      if (url.endsWith(`/api/jobs/${JOB_ID}`)) {
        polls += 1;
        if (polls === 1) {
          return jsonResponse(queuedJob());
        }
        if (polls === 2) {
          return jsonResponse(
            queuedJob({
              status: "translating",
              stage: "translating",
              chunk_current: 12,
              chunk_total: 35,
            }),
          );
        }
        return jsonResponse(
          queuedJob({
            status: "completed",
            stage: "completed",
            chunk_current: 35,
            chunk_total: 35,
            audio_url: `/api/jobs/${JOB_ID}/audio`,
          }),
        );
      }
      return jsonResponse({ error_type: "INVALID_INPUT", message: `unexpected ${url}` }, 500);
    });

    render(<App />);
    await screen.findByLabelText("Paste story");
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    await user.type(screen.getByLabelText("Paste story"), "Once upon a time");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByText("Waiting in queue")).toBeInTheDocument();
    expect(
      await screen.findByText("Translating 12/35", undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    const player = await screen.findByLabelText("Generated audiobook", undefined, {
      timeout: 3000,
    });
    expect(player).toHaveAttribute("src", `/api/jobs/${JOB_ID}/audio`);
    expect(sessionStorage.getItem(LAST_JOB_STORAGE_KEY)).toBe(JOB_ID);
  });

  it("restores the last job from sessionStorage", async () => {
    sessionStorage.setItem(LAST_JOB_STORAGE_KEY, JOB_ID);
    mockFetch((url) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      if (url.endsWith(`/api/jobs/${JOB_ID}`)) {
        return jsonResponse(
          queuedJob({
            status: "completed",
            stage: "completed",
            chunk_current: 3,
            chunk_total: 3,
            audio_url: `/api/jobs/${JOB_ID}/audio`,
          }),
        );
      }
      return jsonResponse({ error_type: "INVALID_INPUT", message: `unexpected ${url}` }, 500);
    });
    render(<App />);
    const player = await screen.findByLabelText("Generated audiobook");
    expect(player).toHaveAttribute("src", `/api/jobs/${JOB_ID}/audio`);
  });

  it("shows error_type from a failed create envelope", async () => {
    const user = userEvent.setup();
    mockFetch((url, init) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      if (url.endsWith("/api/jobs") && init?.method === "POST") {
        return jsonResponse(
          { error_type: "UNSUPPORTED_LANGUAGE", message: "language not available" },
          400,
        );
      }
      return jsonResponse({ error_type: "INVALID_INPUT", message: `unexpected ${url}` }, 500);
    });
    render(<App />);
    await screen.findByLabelText("Paste story");
    await user.type(screen.getByLabelText("Paste story"), "Once upon a time");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("UNSUPPORTED_LANGUAGE: language not available");
    expect(alert.textContent).not.toMatch(/Traceback|at Object\./);
  });
});
