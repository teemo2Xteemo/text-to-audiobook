import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JobProgress } from "./JobProgress";
import { queuedJob } from "../test/fixtures";

describe("JobProgress", () => {
  it("shows stage and chunk counts", () => {
    render(
      <JobProgress
        job={queuedJob({
          status: "translating",
          stage: "translating",
          chunk_current: 12,
          chunk_total: 35,
        })}
      />,
    );
    expect(screen.getByText("Translating 12/35")).toBeInTheDocument();
    expect(screen.getByText(/Stage: translating · chunks 12\/35/)).toBeInTheDocument();
  });

  it("shows error_type and message for a failed job", () => {
    render(
      <JobProgress
        job={queuedJob({
          status: "failed",
          stage: "failed",
          error_type: "TTS_FAILED",
          message: "provider unavailable",
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("TTS_FAILED: provider unavailable");
  });
});
