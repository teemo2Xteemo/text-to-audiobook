import { describe, expect, it } from "vitest";

import type { Voice } from "../api/types";
import {
  canGenerate,
  playerSrc,
  progressCopy,
  resolveSourceDefault,
  resolveTargetDefault,
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
    };
    expect(canGenerate(base)).toBe(false);
    expect(canGenerate({ ...base, text: "Once upon a time" })).toBe(true);
    const file = new File(["story"], "story.txt", { type: "text/plain" });
    expect(canGenerate({ ...base, file })).toBe(true);
    expect(canGenerate({ ...base, text: "paste", file })).toBe(false);
  });
});
