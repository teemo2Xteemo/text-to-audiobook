import { describe, expect, it } from "vitest";

import type { Voice } from "../api/types";
import {
  canGenerate,
  computeProgressPercent,
  estimateTextMetrics,
  isSrtContent,
  playerSrc,
  progressCopy,
  resolveSourceDefault,
  resolveTargetDefault,
  stepVisualState,
  stripSrtToText,
  voicesForTarget,
} from "./jobUi";

const voices: Voice[] = [
  { id: "fake-en", language: "en-US", label: "English A" },
  { id: "fake-ja", language: "ja-JP", label: "Japanese A" },
];

describe("jobUi", () => {
  it("filters voices by target language", () => {
    expect(voicesForTarget(voices, "ja-JP").map((voice) => voice.id)).toEqual(["fake-ja"]);
    expect(voicesForTarget(voices, "en-US").map((voice) => voice.id)).toEqual(["fake-en"]);
  });

  it("uses auto as source default unless env matches a capability", () => {
    expect(resolveSourceDefault("auto", ["en-US", "ja-JP"])).toBe("auto");
    expect(resolveSourceDefault("en-US", ["en-US", "ja-JP"])).toBe("en-US");
    expect(resolveSourceDefault("xx-XX", ["en-US", "ja-JP"])).toBe("auto");
  });

  it("preselects env target only when present in capabilities", () => {
    expect(resolveTargetDefault(["en-US", "ja-JP"], "ja-JP")).toBe("ja-JP");
    expect(resolveTargetDefault(["en-US", "ja-JP"], "ko-KR")).toBe("en-US");
    expect(resolveTargetDefault(["en-US", "ja-JP"], undefined)).toBe("en-US");
  });

  it("builds a same-origin player URL from a relative audio_url", () => {
    expect(playerSrc("", `/api/jobs/abc/audio`)).toBe("/api/jobs/abc/audio");
    expect(playerSrc("http://127.0.0.1:8000", "/api/jobs/abc/audio")).toBe(
      "http://127.0.0.1:8000/api/jobs/abc/audio",
    );
  });

  it("maps status and chunk counts to progress copy", () => {
    expect(progressCopy("queued", 0, 0)).toBe("Waiting in queue");
    expect(progressCopy("translating", 12, 35)).toBe("Translating 12/35");
    expect(progressCopy("generating_audio", 8, 35)).toBe("Generating audio 8/35");
    expect(progressCopy("failed", 0, 0)).toBe("Failed");
  });

  it("requires exactly one of paste or file once a voice exists", () => {
    const base = {
      capabilitiesLoaded: true,
      targetLanguage: "ja-JP",
      hasVoice: true,
      text: "",
      file: null,
      speed: 1,
    };
    expect(canGenerate(base)).toBe(false);
    expect(canGenerate({ ...base, text: "Once upon a time" })).toBe(true);
    const file = new File(["story"], "story.txt", { type: "text/plain" });
    expect(canGenerate({ ...base, file })).toBe(true);
    expect(canGenerate({ ...base, text: "paste", file })).toBe(false);
  });

  it("rejects speed outside 0.5–2.0", () => {
    const ready = {
      capabilitiesLoaded: true,
      targetLanguage: "ja-JP",
      hasVoice: true,
      text: "Once upon a time",
      file: null,
      speed: 1,
    };
    expect(canGenerate(ready)).toBe(true);
    expect(canGenerate({ ...ready, speed: 0.5 })).toBe(true);
    expect(canGenerate({ ...ready, speed: 2 })).toBe(true);
    expect(canGenerate({ ...ready, speed: 0.49 })).toBe(false);
    expect(canGenerate({ ...ready, speed: 2.01 })).toBe(false);
    expect(canGenerate({ ...ready, speed: Number.NaN })).toBe(false);
  });

  it("detects and strips SRT subtitle blocks into clean narrative text", () => {
    const sampleSrt = [
      "1",
      "00:00:01,000 --> 00:00:04,000",
      "<i>Hello world,</i>",
      "this is line one.",
      "",
      "2",
      "00:00:05,000 --> 00:00:08,500",
      "Second subtitle block.",
    ].join("\n");

    expect(isSrtContent(sampleSrt)).toBe(true);
    expect(isSrtContent("Plain story paragraph without timecodes.")).toBe(false);
    expect(stripSrtToText(sampleSrt)).toBe(
      "Hello world, this is line one.\n\nSecond subtitle block.",
    );
  });

  it("does not detect or alter text quoting timecodes without SRT document structure", () => {
    const textWithQuote =
      "Chapter 1\nThe detective noticed 00:00:01,000 --> 00:00:03,000 on the tape.\nCase closed.";
    expect(isSrtContent(textWithQuote)).toBe(false);
    expect(stripSrtToText(textWithQuote)).toBe(textWithQuote);
  });

  it("strips a two-cue SRT to the same output as backend strip_srt", () => {
    const srt =
      "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n2\n00:00:04,000 --> 00:00:06,000\nSecond line.";
    expect(isSrtContent(srt)).toBe(true);
    expect(stripSrtToText(srt)).toBe("Hello world.\n\nSecond line.");
  });

  it("returns empty string when SRT has only timestamps and indices without dialogue", () => {
    const metadataOnlySrt =
      "1\n00:00:01,000 --> 00:00:03,000\n\n2\n00:00:04,000 --> 00:00:06,000\n";
    expect(isSrtContent(metadataOnlySrt)).toBe(true);
    expect(stripSrtToText(metadataOnlySrt)).toBe("");
  });

  it("computes text metrics and pipeline progress percentage", () => {
    const metrics = estimateTextMetrics("One two three");
    expect(metrics.chars).toBe(13);
    expect(metrics.words).toBe(3);
    expect(metrics.estimatedChunks).toBe(1);
    expect(metrics.isSrt).toBe(false);

    expect(computeProgressPercent("queued", 0, 0)).toBe(6);
    expect(computeProgressPercent("translating", 5, 10)).toBe(35);
    expect(computeProgressPercent("completed", 10, 10)).toBe(100);
    expect(stepVisualState("translating", "translating", "queued")).toBe("done");
    expect(stepVisualState("translating", "translating", "translating")).toBe("active");
    expect(stepVisualState("translating", "translating", "merging")).toBe("upcoming");
  });
});
