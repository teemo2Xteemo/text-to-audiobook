import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { JOB_ID, capabilitiesFixture, jsonResponse, queuedJob } from "./test/fixtures";
import { LAST_JOB_STORAGE_KEY, THEME_STORAGE_KEY } from "./lib/constants";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
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

  it("keeps the single audiobook download when a narrate job is one span", async () => {
    sessionStorage.setItem(LAST_JOB_STORAGE_KEY, JOB_ID);
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      if (url.endsWith(`/api/jobs/${JOB_ID}`)) {
        return jsonResponse(
          queuedJob({
            status: "completed",
            stage: "completed",
            mode: "narrate",
            source_language: "en-US",
            target_language: "en-US",
            chunk_current: 1,
            chunk_total: 1,
            chapters_total: 1,
            chapters_ready: 1,
            chapters_url: `/api/jobs/${JOB_ID}/chapters`,
            audio_url: `/api/jobs/${JOB_ID}/audio`,
          }),
        );
      }
      return jsonResponse({ error_type: "INVALID_INPUT", message: `unexpected ${url}` }, 500);
    });
    render(<App />);
    const download = await screen.findByRole("link", { name: "Download Audio File" });
    expect(download).toHaveAttribute("href", `/api/jobs/${JOB_ID}/audio`);
    expect(screen.queryByRole("region", { name: "Chapters" })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/chapters"))).toBe(false);
  });

  it("shows chapter downloads and the audiobook file when several chapters are ready", async () => {
    sessionStorage.setItem(LAST_JOB_STORAGE_KEY, JOB_ID);
    mockFetch((url) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      if (url.includes(`/api/jobs/${JOB_ID}/chapters`)) {
        return jsonResponse({
          job_id: JOB_ID,
          total: 2,
          ready: 2,
          failed: 0,
          matched: 2,
          offset: 0,
          limit: 100,
          partial: false,
          items: [],
        });
      }
      if (url.endsWith(`/api/jobs/${JOB_ID}`)) {
        return jsonResponse(
          queuedJob({
            status: "completed",
            stage: "completed",
            mode: "narrate",
            source_language: "en-US",
            target_language: "en-US",
            chunk_current: 2,
            chunk_total: 2,
            chapters_total: 2,
            chapters_ready: 2,
            chapters_url: `/api/jobs/${JOB_ID}/chapters`,
            zip_url: `/api/jobs/${JOB_ID}/chapters.zip`,
            audio_url: `/api/jobs/${JOB_ID}/audio`,
          }),
        );
      }
      return jsonResponse({ error_type: "INVALID_INPUT", message: `unexpected ${url}` }, 500);
    });
    render(<App />);
    expect(await screen.findByRole("region", { name: "Chapters" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download all (ZIP)" })).toHaveAttribute(
      "href",
      `/api/jobs/${JOB_ID}/chapters.zip`,
    );
    expect(screen.getByRole("link", { name: "Download Audio File" })).toHaveAttribute(
      "href",
      `/api/jobs/${JOB_ID}/audio`,
    );
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

  it("persists theme mode in localStorage and toggles correctly", async () => {
    const user = userEvent.setup();
    mockFetch((url) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      return jsonResponse({}, 200);
    });

    render(<App />);
    const toggleBtn = await screen.findByRole("button", { name: "Toggle color theme" });
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    // Click to toggle to light theme
    await user.click(toggleBtn);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    // Click to toggle back to dark theme
    await user.click(toggleBtn);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("initializes with saved theme from localStorage on load", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    mockFetch((url) => {
      if (url.endsWith("/api/capabilities")) {
        return jsonResponse(capabilitiesFixture);
      }
      return jsonResponse({}, 200);
    });

    render(<App />);
    await screen.findByRole("button", { name: "Toggle color theme" });
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
