import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobForm } from "./JobForm";
import type { Capabilities } from "../api/types";
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

  it("translating mode never lists voices outside the target language even if capabilities has TTS-only voices", () => {
    const capabilitiesWithExtra: Capabilities = {
      ...capabilitiesFixture,
      voices: [
        ...capabilitiesFixture.voices,
        { id: "extra-zh", language: "zh-CN", label: "Chinese TTS Only" },
        { id: "extra-es", language: "es-ES", label: "Spanish TTS Only" },
      ],
    };
    render(
      <JobForm capabilities={capabilitiesWithExtra} disabled={false} onSubmit={() => undefined} />,
    );
    const voice = screen.getByLabelText("Voice");
    const options = within(voice).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["English A"]);
    expect(within(voice).queryByText("Chinese TTS Only")).toBeNull();
    expect(within(voice).queryByText("Spanish TTS Only")).toBeNull();
  });

  it("clears paste when a file is chosen and does not POST both", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("Paste story"), "Once upon a time");
    const file = new File(["uploaded"], "story.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText(/Or upload a \.txt, \.srt, \.vtt, or \.ass file/), file);
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

  it("displays continuous narration indicator for SRT in Narrate mode", async () => {
    const user = userEvent.setup();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />);
    const narrateTab = screen.getByRole("tab", { name: /Narrate/i });
    await user.click(narrateTab);

    const srtText = "1\n00:00:01,000 --> 00:00:03,000\nChapter One begins.";
    await user.type(screen.getByLabelText("Paste story"), srtText);

    expect(screen.getByText("SRT · Continuous Narration")).toBeInTheDocument();
    expect(screen.getByLabelText("Continuous narration")).toBeChecked();
    expect(screen.getByLabelText("Match subtitle timestamps")).not.toBeChecked();
  });

  it("offers text normalization only when the server supports it", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />,
    );
    expect(screen.queryByLabelText(/Spell out numbers/i)).not.toBeInTheDocument();

    rerender(
      <JobForm
        capabilities={{ ...capabilitiesFixture, text_normalization: true }}
        disabled={false}
        onSubmit={onSubmit}
      />,
    );
    // Translate mode never offers it; narrate does.
    expect(screen.queryByLabelText(/Spell out numbers/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Narrate/i }));
    await user.click(screen.getByLabelText(/Spell out numbers/i));
    await user.type(screen.getByLabelText("Paste story"), "It was 20 years ago.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    const payload = onSubmit.mock.calls[0]?.[0] as { normalize_text?: boolean };
    expect(payload.normalize_text).toBe(true);
  });

  it("sends srt_timing only for narrated subtitles", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const srtFile = new File(
      ["1\n00:00:01,000 --> 00:00:03,000\nHello world.\n"],
      "captions.srt",
      { type: "application/x-subrip" },
    );
    await user.upload(screen.getByLabelText(/upload a/i), srtFile);

    // Translate mode never offers or sends a timing choice.
    expect(screen.queryByLabelText("Match subtitle timestamps")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Narrate/i }));
    await user.click(screen.getByLabelText("Match subtitle timestamps"));
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0]?.[0] as { mode?: string; srt_timing?: string };
    expect(payload.mode).toBe("narrate");
    expect(payload.srt_timing).toBe("aligned");
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
      screen.getByText("Only .txt, .srt, .vtt, and .ass files are supported."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects files over the limit reported by capabilities", () => {
    const onSubmit = vi.fn();
    const limit = 1024 * 1024;
    render(
      <JobForm
        capabilities={{ ...capabilitiesFixture, limits: { max_upload_bytes: limit } }}
        disabled={false}
        onSubmit={onSubmit}
      />,
    );
    const bigFile = new File(["x".repeat(100)], "huge.txt", { type: "text/plain" });
    Object.defineProperty(bigFile, "size", { value: 2 * 1024 * 1024 });
    const input = screen.getByLabelText(/upload a/i);
    fireEvent.change(input, { target: { files: [bigFile] } });
    expect(
      screen.getByText("File exceeds maximum allowed size of 1.00 MB (2.00 MB)."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/2 MB/)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("accepts a file under the capabilities limit and above the old 2 MB cap", () => {
    const onSubmit = vi.fn();
    render(
      <JobForm
        capabilities={{ ...capabilitiesFixture, limits: { max_upload_bytes: 5 * 1024 * 1024 } }}
        disabled={false}
        onSubmit={onSubmit}
      />,
    );
    const novel = new File(["chapter"], "novel.txt", { type: "text/plain" });
    Object.defineProperty(novel, "size", { value: 3 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText(/upload a/i), { target: { files: [novel] } });
    expect(screen.queryByText(/exceeds maximum allowed size/)).toBeNull();
    expect(screen.getByText("novel.txt")).toBeInTheDocument();
  });

  it("falls back to MAX_FILE_SIZE_BYTES when capabilities omit the limit", () => {
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const bigFile = new File(["x"], "huge.txt", { type: "text/plain" });
    Object.defineProperty(bigFile, "size", { value: 52_428_800 + 1 });
    fireEvent.change(screen.getByLabelText(/upload a/i), { target: { files: [bigFile] } });
    expect(
      screen.getByText(/File exceeds maximum allowed size of 50.00 MB/),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("blocks pasted text whose UTF-8 byte length exceeds the limit", () => {
    const onSubmit = vi.fn();
    render(
      <JobForm
        capabilities={{ ...capabilitiesFixture, limits: { max_upload_bytes: 3 } }}
        disabled={false}
        onSubmit={onSubmit}
      />,
    );
    const story = screen.getByLabelText("Paste story");
    fireEvent.change(story, { target: { value: "你" } });
    expect(screen.queryByText(/Paste exceeds/)).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled();

    fireEvent.change(story, { target: { value: "你好" } });
    expect(
      screen.getByText("Paste exceeds maximum allowed size of 3 B (6 B)."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    fireEvent.submit(story.closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects invalid files dropped via drag and drop", () => {
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const badFile = new File(["binary content"], "archive.zip", {
      type: "application/zip",
    });
    const dropzone = screen
      .getByText(/Or upload a \.txt, \.srt, \.vtt, or \.ass file/i)
      .closest(".upload-dropzone");
    expect(dropzone).not.toBeNull();
    fireEvent.drop(dropzone!, {
      dataTransfer: {
        files: [badFile],
      },
    });
    expect(
      screen.getByText("Only .txt, .srt, .vtt, and .ass files are supported."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("renders Narrate mode toggle with No model cost label", () => {
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    expect(screen.getByText(/Narrate/i)).toBeInTheDocument();
    expect(screen.getByText(/No model cost/i)).toBeInTheDocument();
  });

  it("shows switch banner when source equals target in translate mode", async () => {
    const user = userEvent.setup();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("Source language"), "ja-JP");
    await user.selectOptions(screen.getByLabelText("Target language"), "ja-JP");
    expect(
      screen.getByText(/Same language — switch to Narrate directly\?/i),
    ).toBeInTheDocument();

    const switchBtn = screen.getByRole("button", { name: /switch to narrate/i });
    await user.click(switchBtn);
    expect(screen.getByText(/No translation needed/i)).toBeInTheDocument();
  });

  it("switches to Narrate mode and retains selected language", async () => {
    const user = userEvent.setup();
    const mockCaps = {
      ...capabilitiesFixture,
      narrate_languages: ["en-US", "ja-JP"],
    };
    render(<JobForm capabilities={mockCaps} disabled={false} onSubmit={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("Target language"), "ja-JP");
    const narrateTab = screen.getByRole("tab", { name: /Narrate/i });
    await user.click(narrateTab);

    expect(screen.queryByLabelText("Target language")).toBeNull();
    expect(screen.getByText(/No translation needed/i)).toBeInTheDocument();
    const voiceSelect = screen.getByLabelText("Voice");
    expect(within(voiceSelect).getByText("Japanese A")).toBeInTheDocument();
  });

  it("submits job with mode in payload", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    const narrateTab = screen.getByRole("tab", { name: /Narrate/i });
    await user.click(narrateTab);

    await user.type(screen.getByLabelText("Paste story"), "A short narration.");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: "narrate",
        text: "A short narration.",
      }),
    );
  });

  it("displays analysis panel and handles preview tts", async () => {
    const user = userEvent.setup();
    const fakeAudio = { audio_base64: "AAAA", mime_type: "audio/mpeg" };
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "en-US",
      confidence: 0.95,
      needs_confirmation: false,
      candidates: [{ language: "en-US", confidence: 0.95 }],
      chapters_count: 3,
      chapters_preview: [{ index: 0, title: "Ch 1", char_count: 50 }],
      duration_estimate_seconds: 120,
      process_time_estimate_seconds: 18,
      preview_lines: ["Line 1", "Line 2"],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/analyze")) {
          return new Response(JSON.stringify(fakeAnalyze), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/api/tts/preview")) {
          return new Response(JSON.stringify(fakeAudio), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.type(screen.getByLabelText("Paste story"), "Chapter 1 text here.");

    expect(await screen.findByText(/3 chapters/i)).toBeInTheDocument();
    expect(screen.getByText(/Detected encoding/i)).toBeInTheDocument();
    expect(screen.getByText(/2m/i)).toBeInTheDocument();

    const listenBtn = screen.getByRole("button", { name: /Listen/i });
    expect(listenBtn).toBeInTheDocument();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
    await user.click(listenBtn);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/tts/preview"),
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("shows chapter kinds and warns when detection is uncertain", async () => {
    const user = userEvent.setup();
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "vi-VN",
      confidence: 0.95,
      needs_confirmation: false,
      candidates: [{ language: "vi-VN", confidence: 0.95 }],
      chapters_count: 1,
      chapter_detection_confidence: 0.62,
      chapter_detection_uncertain: true,
      chapters_preview: [
        { index: 1, ordinal: 1, kind: "preface", title: "", char_count: 12, chapter_number: null },
        {
          index: 2,
          ordinal: 2,
          kind: "volume",
          title: "Quyển 1: Sơn Môn",
          char_count: 0,
          chapter_number: null,
        },
        {
          index: 3,
          ordinal: 3,
          kind: "chapter",
          title: "Chương 1: Mở đầu",
          char_count: 40,
          chapter_number: 1,
        },
      ],
      duration_estimate_seconds: 30,
      process_time_estimate_seconds: 4,
      preview_lines: ["Lời nói đầu"],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/analyze")) {
          return new Response(JSON.stringify(fakeAnalyze), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.type(screen.getByLabelText("Paste story"), "Chương 1");

    expect(
      await screen.findByText(
        "Chapter detection is uncertain — please check the list before generating.",
      ),
    ).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "Detected chapters" })).getAllByRole(
      "listitem",
    );
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Preface");
    expect(items[1]).toHaveTextContent("Volume");
    expect(items[1]).toHaveTextContent("Quyển 1: Sơn Môn");
    expect(items[2]).toHaveTextContent("Chapter");
    expect(items[2]).toHaveTextContent("Chương 1: Mở đầu");
  });

  it("displays friendly error message when preview tts times out", async () => {
    const user = userEvent.setup();
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "en-US",
      confidence: 0.95,
      needs_confirmation: false,
      candidates: [{ language: "en-US", confidence: 0.95 }],
      chapters_count: 1,
      chapters_preview: [],
      duration_estimate_seconds: 30,
      process_time_estimate_seconds: 4,
      preview_lines: ["Snippet"],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/analyze")) {
          return new Response(JSON.stringify(fakeAnalyze), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/api/tts/preview")) {
          return new Response(
            JSON.stringify({
              error_type: "TIMEOUT",
              message: "Preview generation timed out after 15s",
            }),
            {
              status: 504,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        return new Response("{}", { status: 404 });
      }),
    );

    render(
      <JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={() => undefined} />,
    );

    await user.type(screen.getByLabelText("Paste story"), "Chapter 1 text here.");
    const listenBtn = await screen.findByRole("button", { name: /Listen/i });
    await user.click(listenBtn);
    expect(
      await screen.findByText("The request timed out. Please try again with shorter text."),
    ).toBeInTheDocument();
  });

  it("shows confirmation banner when detection confidence is low or needs confirmation", async () => {
    const user = userEvent.setup();
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "ja-JP",
      confidence: 0.55,
      needs_confirmation: true,
      candidates: [
        { language: "ja-JP", confidence: 0.55 },
        { language: "en-US", confidence: 0.45 },
      ],
      chapters_count: 1,
      chapters_preview: [],
      duration_estimate_seconds: 30,
      process_time_estimate_seconds: 4,
      preview_lines: ["Snippet"],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/analyze")) {
          return new Response(JSON.stringify(fakeAnalyze), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.type(screen.getByLabelText("Paste story"), "Ambiguous text snippet");

    expect(await screen.findByText(/confirm the language/i)).toBeInTheDocument();
  });

  it("populates voices and enables generation when pasting text with Auto in Narrate mode", async () => {
    const user = userEvent.setup();
    const fakeAnalyze = {
      encoding: "UTF-8",
      language: "en-US",
      confidence: 0.95,
      needs_confirmation: false,
      candidates: [{ language: "en-US", confidence: 0.95 }],
      chapters_count: 1,
      chapters_preview: [],
      duration_estimate_seconds: 30,
      process_time_estimate_seconds: 4,
      preview_lines: ["Once upon a time in a faraway land."],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/analyze")) {
          return new Response(JSON.stringify(fakeAnalyze), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    const narrateTab = screen.getByRole("tab", { name: /Narrate/i });
    await user.click(narrateTab);

    // Set source language to auto
    await user.selectOptions(screen.getByLabelText("Source language"), "auto");
    const generateBtn = screen.getByRole("button", { name: "Generate" });
    expect(generateBtn).toBeDisabled();

    // Paste text into story area
    await user.type(screen.getByLabelText("Paste story"), "Once upon a time in a faraway land.");

    // Wait for analysis to arrive and voices to populate
    const voiceSelect = screen.getByLabelText("Voice");
    expect(await within(voiceSelect).findByText("English A")).toBeInTheDocument();
    expect(generateBtn).toBeEnabled();
  });

  describe("TTS engine picker", () => {
    const enginesCaps: Capabilities = {
      ...capabilitiesFixture,
      tts_engines: [
        { id: "edge", label: "Edge", kind: "online", cost_label: "Free", available: true, languages: ["en-US", "ja-JP"], max_concurrency: 4 },
        { id: "fish", label: "Fish Audio", kind: "online", cost_label: "Paid", available: true, languages: ["en-US"], max_concurrency: 2 },
        { id: "vieneu", label: "VieNeu", kind: "local", cost_label: "Local", available: false, languages: ["en-US"], max_concurrency: 1 },
      ],
      voices: [
        { id: "fake-en", language: "en-US", label: "English A", engine: "edge" },
        { id: "fake-ja", language: "ja-JP", label: "Japanese A", engine: "edge" },
        { id: "fish:en", language: "en-US", label: "Fish English", engine: "fish" },
      ],
    };

    beforeEach(() => localStorage.clear());

    it("shows a labelled picker with cost labels and disabled unavailable engines", () => {
      render(<JobForm capabilities={enginesCaps} disabled={false} onSubmit={() => undefined} />);
      const picker = screen.getByLabelText("Voice engine");
      const options = within(picker).getAllByRole("option") as HTMLOptionElement[];
      expect(options.map((o) => o.textContent)).toEqual([
        "Edge · Free",
        "Fish Audio · Paid",
        "VieNeu · Local · Not configured",
      ]);
      expect(options[2]).toBeDisabled();
      expect(options[1]).toBeEnabled();
    });

    it("hides the picker with a single engine or none", () => {
      render(
        <JobForm
          capabilities={{ ...enginesCaps, tts_engines: enginesCaps.tts_engines?.slice(0, 1) }}
          disabled={false}
          onSubmit={() => undefined}
        />,
      );
      expect(screen.queryByLabelText("Voice engine")).toBeNull();
    });

    it("filters voices by engine, resets the voice, and sends tts_engine only when not default", async () => {
      const user = userEvent.setup();
      const onSubmit = vi.fn();
      render(<JobForm capabilities={enginesCaps} disabled={false} onSubmit={onSubmit} />);
      const voice = screen.getByLabelText("Voice");
      expect(within(voice).getAllByRole("option").map((o) => o.textContent)).toEqual(["English A"]);

      await user.type(screen.getByLabelText("Paste story"), "Hello");
      await user.click(screen.getByRole("button", { name: "Generate" }));
      expect(onSubmit.mock.calls[0]?.[0].ttsEngine).toBeUndefined();

      await user.selectOptions(screen.getByLabelText("Voice engine"), "fish");
      expect(within(voice).getAllByRole("option").map((o) => o.textContent)).toEqual(["Fish English"]);
      expect(voice).toHaveValue("fish:en");
      await user.click(screen.getByRole("button", { name: "Generate" }));
      expect(onSubmit.mock.calls[1]?.[0]).toMatchObject({ ttsEngine: "fish", voice: "fish:en" });
    });

    it("sends tts_engine on preview", async () => {
      const user = userEvent.setup();
      const ok = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
        ok(
          url.includes("/api/analyze")
            ? { encoding: "UTF-8", language: "en-US", confidence: 1, needs_confirmation: false, candidates: [], chapters_count: 1, chapters_preview: [], duration_estimate_seconds: 1, process_time_estimate_seconds: 1, preview_lines: [] }
            : { audio_base64: "AAAA", mime_type: "audio/mpeg" },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);
      vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
      render(<JobForm capabilities={enginesCaps} disabled={false} onSubmit={() => undefined} />);
      await user.selectOptions(screen.getByLabelText("Voice engine"), "fish");
      await user.type(screen.getByLabelText("Paste story"), "Hello there");
      await user.click(await screen.findByRole("button", { name: /Listen/i }));
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/tts/preview"));
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        voice: "fish:en",
        tts_engine: "fish",
      });
      vi.unstubAllGlobals();
    });

    it("remembers the last engine in localStorage", async () => {
      const user = userEvent.setup();
      const { unmount } = render(
        <JobForm capabilities={enginesCaps} disabled={false} onSubmit={() => undefined} />,
      );
      await user.selectOptions(screen.getByLabelText("Voice engine"), "fish");
      unmount();
      render(<JobForm capabilities={enginesCaps} disabled={false} onSubmit={() => undefined} />);
      expect(screen.getByLabelText("Voice engine")).toHaveValue("fish");
    });

    it("offers Use Edge instead after a provider configuration error, without resubmitting", async () => {
      const user = userEvent.setup();
      const onSubmit = vi.fn();
      localStorage.setItem("audiobook_studio_tts_engine", "fish");
      render(
        <JobForm
          capabilities={enginesCaps}
          disabled={false}
          onSubmit={onSubmit}
          createErrorType="PROVIDER_CONFIGURATION_ERROR"
        />,
      );
      await user.click(screen.getByRole("button", { name: "Use Edge instead" }));
      expect(screen.getByLabelText("Voice engine")).toHaveValue("edge");
      expect(onSubmit).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Use Edge instead" })).toBeNull();
    });
  });

  it("subtitle tab hides voice controls and defaults to txt", async () => {
    const user = userEvent.setup();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("tab", { name: "Translate only" }));
    expect(screen.queryByLabelText(/^voice$/i)).toBeNull();
    expect(screen.queryByLabelText(/^speed$/i)).toBeNull();
    expect(screen.getByLabelText("Translation engine")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ".txt" })).toHaveAttribute("aria-pressed", "true");
  });

  it("uploading .srt locks format to srt", async () => {
    const user = userEvent.setup();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("tab", { name: "Translate only" }));
    const file = new File(
      ["1\n00:00:01,000 --> 00:00:02,000\nHi\n"],
      "notes.txt",
      { type: "application/x-subrip" },
    );
    fireEvent.change(screen.getByLabelText(/upload a/i), { target: { files: [file] } });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: ".srt" })).toHaveAttribute("aria-pressed", "true");
    });
    expect(screen.getByRole("button", { name: ".txt" })).toBeDisabled();
  });

  it("submits mode subtitle with document_format", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<JobForm capabilities={capabilitiesFixture} disabled={false} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("tab", { name: "Translate only" }));
    await user.click(screen.getByRole("button", { name: ".vtt" }));
    fireEvent.change(screen.getByLabelText("Paste story"), {
      target: { value: "Hello there." },
    });
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "subtitle", document_format: "vtt" }),
    );
  });
});
