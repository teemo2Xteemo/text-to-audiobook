import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

  it("shows friendly error summary with expandable technical details", () => {
    render(
      <JobProgress
        job={queuedJob({
          status: "failed",
          stage: "failed",
          error_type: "PROVIDER_RATE_LIMIT",
          message: "Too many requests",
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The voice service is limiting free usage");
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByText("PROVIDER_RATE_LIMIT: Too many requests")).toBeInTheDocument();
  });

  it("offers Continue with Edge only for an engine failure, with the mix warning", async () => {
    const user = userEvent.setup();
    const onContinue = vi.fn();
    const warning = "Later chapters use Edge. Finished chapters keep their original voice.";
    render(
      <JobProgress
        job={queuedJob({
          status: "failed",
          stage: "failed",
          error_type: "PROVIDER_RATE_LIMIT",
          message: "tts rate limited",
          tts_engine: "fish",
          continue_with_edge: true,
          tts_mix_warning: warning,
        })}
        onContinueWithEdge={onContinue}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(warning);
    await user.click(screen.getByRole("button", { name: "Continue with Edge" }));
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it("hides Continue with Edge when the failure is not a voice-engine error", () => {
    render(
      <JobProgress
        job={queuedJob({
          status: "failed",
          stage: "failed",
          error_type: "TRANSLATION_FAILED",
          message: "translation failed",
          continue_with_edge: false,
        })}
        onContinueWithEdge={() => undefined}
      />,
    );
    expect(screen.queryByRole("button", { name: "Continue with Edge" })).toBeNull();
  });

  it("shows an Edge availability error next to the button", () => {
    render(
      <JobProgress
        job={queuedJob({
          status: "failed",
          stage: "failed",
          error_type: "TTS_FAILED",
          message: "tts connection failed",
          continue_with_edge: true,
          tts_mix_warning: "Later chapters use Edge. Finished chapters keep their original voice.",
        })}
        onContinueWithEdge={() => undefined}
        continueError="Edge is not available right now"
      />,
    );
    expect(screen.getByRole("button", { name: "Continue with Edge" })).toBeInTheDocument();
    expect(screen.getByText("Edge is not available right now")).toBeInTheDocument();
    expect(screen.getByText(/Speech synthesis failed/)).toBeInTheDocument();
  });
});
