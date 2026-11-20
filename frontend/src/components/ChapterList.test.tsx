import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JOB_ID, jsonResponse, queuedJob } from "../test/fixtures";
import { ChapterList } from "./ChapterList";

const chapters = {
  job_id: JOB_ID,
  total: 800,
  ready: 412,
  failed: 1,
  matched: 800,
  offset: 0,
  limit: 100,
  partial: true,
  items: [
    {
      ordinal: 1,
      index: 1,
      kind: "chapter",
      title: "Chương 1 Thiếu niên",
      title_source: "第一章",
      status: "ready",
      chunk_first: 1,
      chunk_last: 2,
      duration_ms: 462000,
      size_bytes: 7395000,
      filename: "0001 - Chương 1 Thiếu niên.mp3",
      download_url: `/api/jobs/${JOB_ID}/chapters/1/audio`,
      tts_engine: "fish",
    },
    {
      ordinal: 2,
      index: 2,
      kind: "chapter",
      title: "Chương 2",
      title_source: "第二章",
      status: "pending",
      chunk_first: 3,
      chunk_last: 4,
      filename: "0002 - Chương 2.mp3",
      download_url: null,
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ChapterList", () => {
  it("hides the list when the book is a single chapter", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <ChapterList job={queuedJob({ chapters_total: 1, chapters_ready: 0 })} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists ready chapters, labels a partial zip, and refetches only when counts change", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(chapters)));
    vi.stubGlobal("fetch", fetchMock);
    const job = queuedJob({
      status: "generating_audio",
      stage: "generating_audio",
      chapters_total: 800,
      chapters_ready: 412,
      chapters_failed: 1,
      chapters_url: `/api/jobs/${JOB_ID}/chapters`,
      zip_url: `/api/jobs/${JOB_ID}/chapters.zip`,
    });
    const view = render(<ChapterList job={job} />);
    expect(await screen.findByText("Chương 1 Thiếu niên")).toBeInTheDocument();
    expect(screen.getByText("ready · fish")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download ready chapters \(412\/800\) as ZIP/ })).toHaveAttribute(
      "href",
      `/api/jobs/${JOB_ID}/chapters.zip`,
    );
    const download = screen.getByRole("link", { name: "Download" });
    expect(download).toHaveAttribute("href", `/api/jobs/${JOB_ID}/chapters/1/audio?download=1`);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    view.rerender(
      <ChapterList
        job={{ ...job, chunk_current: job.chunk_current + 1 }}
      />,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);

    view.rerender(<ChapterList job={{ ...job, chapters_ready: 413 }} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("heading", { name: /413/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Play" }));
    expect(screen.getByLabelText("Chapter audio")).toHaveAttribute(
      "src",
      `/api/jobs/${JOB_ID}/chapters/1/audio`,
    );
  });
});
