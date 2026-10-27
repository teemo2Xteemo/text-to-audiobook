import { fireEvent, render, screen, within } from "@testing-library/react";
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
    await user.upload(screen.getByLabelText("Or upload a .txt or .srt file"), file);
    expect(screen.getByLabelText("Paste story")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0]?.[0] as { text?: string; file?: File };
    expect(payload.file).toBe(file);
    expect(payload.text).toBeUndefined();
  });

  it("offers one-click SRT timestamp cleaning when SRT content is pasted", async () => {
    const user = userEvent.setup();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />);
    const srtText = "1\n00:00:01,000 --> 00:00:03,000\nChapter One begins.";
    await user.type(screen.getByLabelText("Paste story"), srtText);
    const cleanBtn = await screen.findByRole("button", { name: "Strip SRT timestamps" });
    await user.click(cleanBtn);
    expect(screen.getByLabelText("Paste story")).toHaveValue("Chapter One begins.");
  });

  it("provides an accessible swap button that exchanges source and target languages", async () => {
    const user = userEvent.setup();
    render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />,
    );
    const swapBtn = screen.getByRole("button", { name: /swap/i });
    expect(swapBtn).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Source language"), "ja-JP");
    expect(swapBtn).not.toBeDisabled();
    expect(screen.getByLabelText("Source language")).toHaveValue("ja-JP");
    expect(screen.getByLabelText("Target language")).toHaveValue("en-US");

    await user.click(swapBtn);
    expect(screen.getByLabelText("Source language")).toHaveValue("en-US");
    expect(screen.getByLabelText("Target language")).toHaveValue("ja-JP");
  });

  it("accepts uploaded .srt file as-is and reaches onSubmit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const srtFile = new File(
      ["1\n00:00:01,000 --> 00:00:03,000\nHello world.\n"],
      "captions.srt",
      { type: "application/x-subrip" },
    );
    await user.upload(screen.getByLabelText(/upload a/i), srtFile);
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0]?.[0] as { file?: File };
    expect(payload.file).toBe(srtFile);
    expect(payload.file?.name).toBe("captions.srt");
  });

  it("tabs to the voice select and changes it with the keyboard", async () => {
    const user = userEvent.setup();
    render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />,
    );
    const voiceSelect = screen.getByLabelText("Voice");
    voiceSelect.focus();
    expect(voiceSelect).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(voiceSelect).toHaveValue("fake-en");
  });

  it("renders translation profile select when capabilities include profiles", async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn();
    const capabilitiesWithProfiles = {
      ...capabilitiesFixture,
      translation_profiles: [
        {
          id: "gemini-3.5-flash",
          provider: "gemini",
          model: "gemini-3.5-flash",
          label: "Google Gemini 3.5 Flash",
          badge: "ONLINE",
          description: "Văn phong tự nhiên · Khuyên dùng",
          is_default: true,
        },
        {
          id: "nllb-600m",
          provider: "nllb",
          model: "nllb-200-distilled-600M",
          label: "NLLB-200 (CPU Offline)",
          badge: "OFFLINE",
          description: "Chạy hoàn toàn trên máy · Riêng tư",
          is_default: false,
        },
      ],
    };

    render(
      <JobForm
        capabilities={capabilitiesWithProfiles}
        disabled={false}
        onSubmit={handleSubmit}
      />,
    );

    const profileSelect = screen.getByLabelText("Translation engine");
    expect(profileSelect).toHaveValue("gemini-3.5-flash");

    const textarea = screen.getByLabelText("Paste story");
    await user.type(textarea, "A tale of stars.");
    const submitBtn = screen.getByRole("button", { name: "Generate" });
    await user.click(submitBtn);

    expect(handleSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        translation_profile_id: "gemini-3.5-flash",
      }),
    );
  });

  it("allows changing translation profile selection and submits chosen profile", async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn();
    const capabilitiesWithProfiles = {
      ...capabilitiesFixture,
      translation_profiles: [
        {
          id: "gemini-3.5-flash",
          provider: "gemini",
          model: "gemini-3.5-flash",
          label: "Google Gemini 3.5 Flash",
          badge: "ONLINE",
          description: "Natural tone · Recommended",
          is_default: true,
        },
        {
          id: "nllb-600m",
          provider: "nllb",
          model: "nllb-200-distilled-600M",
          label: "NLLB-200 (CPU Offline)",
          badge: "OFFLINE",
          description: "Runs locally on CPU · Private",
          is_default: false,
        },
      ],
    };

    render(
      <JobForm
        capabilities={capabilitiesWithProfiles}
        disabled={false}
        onSubmit={handleSubmit}
      />,
    );

    const profileSelect = screen.getByLabelText("Translation engine");
    await user.selectOptions(profileSelect, "nllb-600m");
    expect(profileSelect).toHaveValue("nllb-600m");

    const textarea = screen.getByLabelText("Paste story");
    await user.type(textarea, "Changing translation engine story.");
    const submitBtn = screen.getByRole("button", { name: "Generate" });
    await user.click(submitBtn);

    expect(handleSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        translation_profile_id: "nllb-600m",
      }),
    );
  });

  it("hides translation profile select when capabilities have empty profiles", async () => {
    const user = userEvent.setup();
    const handleSubmit = vi.fn();
    const capabilitiesWithoutProfiles = {
      ...capabilitiesFixture,
      translation_profiles: [],
    };

    render(
      <JobForm
        capabilities={capabilitiesWithoutProfiles}
        disabled={false}
        onSubmit={handleSubmit}
      />,
    );

    expect(screen.queryByLabelText("Translation engine")).toBeNull();

    const textarea = screen.getByLabelText("Paste story");
    await user.type(textarea, "No profiles available.");
    const submitBtn = screen.getByRole("button", { name: "Generate" });
    await user.click(submitBtn);

    expect(handleSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        translation_profile_id: undefined,
      }),
    );
  });

  it("rejects unsupported file formats and displays an error", () => {
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const badFile = new File(["binary content"], "program.exe", {
      type: "application/x-msdownload",
    });
    const input = screen.getByLabelText(/upload a/i);
    fireEvent.change(input, { target: { files: [badFile] } });
    expect(
      screen.getByText("Only .txt, .srt, and .vtt files are supported."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects files that exceed the 2MB size limit", () => {
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const bigFile = new File(["x".repeat(100)], "huge.txt", { type: "text/plain" });
    Object.defineProperty(bigFile, "size", { value: 3 * 1024 * 1024 });
    const input = screen.getByLabelText(/upload a/i);
    fireEvent.change(input, { target: { files: [bigFile] } });
    expect(
      screen.getByText(/File exceeds maximum allowed size of 2 MB/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects invalid files dropped via drag and drop", () => {
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const badFile = new File(["binary content"], "archive.zip", {
      type: "application/zip",
    });
    const dropzone = screen.getByText(/Or upload a \.txt or \.srt file/i).closest(".upload-dropzone");
    expect(dropzone).not.toBeNull();
    fireEvent.drop(dropzone!, {
      dataTransfer: {
        files: [badFile],
      },
    });
    expect(
      screen.getByText("Only .txt, .srt, and .vtt files are supported."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
