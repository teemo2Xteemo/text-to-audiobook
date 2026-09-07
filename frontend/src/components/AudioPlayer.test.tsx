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
});
