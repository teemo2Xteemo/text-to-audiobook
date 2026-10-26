import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AudioPlayer } from "./AudioPlayer";

describe("AudioPlayer", () => {
  it("shows an alert when the media element fails to load", () => {
    render(<AudioPlayer src="/api/jobs/example/audio" />);
    fireEvent.error(screen.getByLabelText("Generated audiobook"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The browser could not play this audio file.",
    );
  });

  it("omits subtitle download link when subtitlesSrc is omitted", () => {
    render(<AudioPlayer src="/api/jobs/example/audio" />);
    expect(
      screen.queryByRole("link", { name: "Download Subtitles (.srt)" }),
    ).not.toBeInTheDocument();
  });

  it("renders subtitle download link when subtitlesSrc is provided", () => {
    render(
      <AudioPlayer
        src="/api/jobs/example/audio"
        subtitlesSrc="/api/jobs/custom/subtitles"
      />,
    );
    const link = screen.getByRole("link", { name: "Download Subtitles (.srt)" });
    expect(link).toHaveAttribute("href", "/api/jobs/custom/subtitles");
    expect(link).toHaveAttribute("download");
  });

  it("omits subtitle download link when subtitlesSrc is null", () => {
    render(<AudioPlayer src="/api/jobs/example/audio" subtitlesSrc={null} />);
    expect(
      screen.queryByRole("link", { name: "Download Subtitles (.srt)" }),
    ).not.toBeInTheDocument();
  });
});
