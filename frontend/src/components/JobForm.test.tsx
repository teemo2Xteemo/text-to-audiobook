import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { JobForm } from "./JobForm";
import { capabilitiesFixture } from "../test/fixtures";

describe("JobForm", () => {
  it("puts Auto only on source and lists capability languages", () => {
    render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />,
    );
    const source = screen.getByLabelText("Source language");
    const sourceValues = within(source)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(sourceValues[0]).toBe("auto");
    expect(sourceValues).toContain("en-US");
    expect(sourceValues).toContain("ja-JP");

    const target = screen.getByLabelText("Target language");
    const targetValues = within(target)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(targetValues).not.toContain("auto");
    expect(targetValues).toEqual(["en-US", "ja-JP"]);
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("filters voices by target and drops the previous language when target changes", async () => {
    const user = userEvent.setup();
    render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />,
    );
    const voice = screen.getByLabelText("Voice");
    expect(within(voice).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "English A",
    ]);

    await user.selectOptions(screen.getByLabelText("Target language"), "ja-JP");
    expect(within(voice).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Japanese A",
    ]);
    expect(within(voice).queryByText("English A")).toBeNull();
  });

  it("clears paste when a file is chosen and does not POST both", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("Paste story"), "Once upon a time");
    const file = new File(["uploaded"], "story.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("Or upload a .txt file"), file);
    expect(screen.getByLabelText("Paste story")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0]?.[0] as { text?: string; file?: File };
    expect(payload.file).toBe(file);
    expect(payload.text).toBeUndefined();
  });
});
