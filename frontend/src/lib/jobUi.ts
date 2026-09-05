import { AUTO_SOURCE_LANGUAGE, SPEED_MAX, SPEED_MIN } from "./constants";
import type { JobStatus, Voice } from "../api/types";

export function voicesForTarget(voices: readonly Voice[], targetLanguage: string): Voice[] {
  return voices.filter((voice) => voice.language === targetLanguage);
}

export function resolveSourceDefault(envSource: string, languages: readonly string[]): string {
  const value = envSource.trim() || AUTO_SOURCE_LANGUAGE;
  if (value === AUTO_SOURCE_LANGUAGE || languages.includes(value)) {
    return value;
  }
  return AUTO_SOURCE_LANGUAGE;
}

export function resolveTargetDefault(
  languages: readonly string[],
  envTarget: string | undefined,
): string {
  if (envTarget && languages.includes(envTarget)) {
    return envTarget;
  }
  return languages[0] ?? "";
}

export function playerSrc(apiBase: string, audioUrl: string): string {
  const base = apiBase.replace(/\/$/, "");
  if (audioUrl.startsWith("http://") || audioUrl.startsWith("https://")) {
    return audioUrl;
  }
  return `${base}${audioUrl}`;
}

export function isTerminalStatus(status: JobStatus): boolean {
  return status === "completed" || status === "failed";
}

export function canGenerate(input: {
  capabilitiesLoaded: boolean;
  targetLanguage: string;
  hasVoice: boolean;
  text: string;
  file: File | null;
  speed: number;
}): boolean {
  if (!input.capabilitiesLoaded || !input.targetLanguage || !input.hasVoice) {
    return false;
  }
  if (!Number.isFinite(input.speed) || input.speed < SPEED_MIN || input.speed > SPEED_MAX) {
    return false;
  }
  const hasText = input.text.trim().length > 0;
  const hasFile = input.file !== null;
  return hasText !== hasFile;
}

export function progressCopy(
  status: JobStatus,
  chunkCurrent: number,
  chunkTotal: number,
): string {
  switch (status) {
    case "queued":
      return "Waiting in queue";
    case "parsing":
      return "Reading story";
    case "translating":
      return chunkTotal > 0 ? `Translating ${chunkCurrent}/${chunkTotal}` : "Translating";
    case "preparing_tts":
      return "Preparing narration";
    case "generating_audio":
      return chunkTotal > 0
        ? `Generating audio ${chunkCurrent}/${chunkTotal}`
        : "Generating audio";
    case "merging":
      return "Merging";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
  }
}

export function languageLabel(code: string): string {
  if (code === AUTO_SOURCE_LANGUAGE) {
    return "Auto";
  }
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(code);
    return name ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}
