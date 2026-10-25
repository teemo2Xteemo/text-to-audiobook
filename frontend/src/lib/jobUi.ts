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

export function languageDisplayName(code: string): string {
  if (code === AUTO_SOURCE_LANGUAGE) {
    return "Auto-detect";
  }
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(code);
    return name || code;
  } catch {
    return code;
  }
}

export function voiceDisplayParts(label: string, id: string): { title: string; subtitle: string } {
  const cleaned = label
    .replace(/^Microsoft\s+/i, "")
    .replace(/\s+Online\s+\(Natural\)\s*-\s*/i, " (Natural) · ")
    .trim();
  return {
    title: cleaned || label,
    subtitle: id !== label ? `Voice ID · ${id}` : "Neural narrator",
  };
}

const SRT_TIMECODE_LINE_RE =
  /^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$/;
const SRT_INDEX_LINE_RE = /^\s*\d+\s*$/;
const SRT_HTML_TAG_RE = /<\/?[a-zA-Z][^>]*>/g;

function isSrtCueBlock(block: string): boolean {
  const lines = block
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return false;
  }
  if (SRT_TIMECODE_LINE_RE.test(lines[0])) {
    return true;
  }
  return (
    lines.length >= 2 &&
    SRT_INDEX_LINE_RE.test(lines[0]) &&
    SRT_TIMECODE_LINE_RE.test(lines[1])
  );
}

function isSrtDocument(blocks: string[]): boolean {
  const nonEmpty = blocks.map((b) => b.trim()).filter(Boolean);
  if (nonEmpty.length === 0) {
    return false;
  }
  const first = nonEmpty[0].replace(/^\ufeff/, "");
  const firstLines = first
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (firstLines.length > 0 && firstLines[0].toUpperCase().startsWith("WEBVTT")) {
    const remaining = firstLines.slice(1).join("\n");
    if (remaining && isSrtCueBlock(remaining)) {
      return true;
    }
    return nonEmpty.length >= 2 && isSrtCueBlock(nonEmpty[1]);
  }
  return isSrtCueBlock(first);
}

export function isSrtContent(text: string): boolean {
  if (!text.trim()) {
    return false;
  }
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const blocks = normalized.split(/\n{2,}/);
  return isSrtDocument(blocks);
}

export function stripSrtToText(raw: string): string {
  const normalized = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const blocks = normalized.split(/\n{2,}/);
  if (!isSrtDocument(blocks)) {
    return raw;
  }

  const cleanedBlocks: string[] = [];

  for (const block of blocks) {
    const rawLines = block.split("\n");
    const cueLines: string[] = [];
    let i = 0;
    while (i < rawLines.length) {
      const line = rawLines[i].trim();
      if (!line || line.toUpperCase() === "WEBVTT") {
        i++;
        continue;
      }
      if (SRT_INDEX_LINE_RE.test(line)) {
        let nextIdx = i + 1;
        while (nextIdx < rawLines.length && !rawLines[nextIdx].trim()) {
          nextIdx++;
        }
        if (nextIdx < rawLines.length && SRT_TIMECODE_LINE_RE.test(rawLines[nextIdx].trim())) {
          i = nextIdx;
          continue;
        }
      }
      if (SRT_TIMECODE_LINE_RE.test(line)) {
        i++;
        continue;
      }
      const cleaned = line.replace(SRT_HTML_TAG_RE, "").trim();
      if (cleaned) {
        cueLines.push(cleaned);
      }
      i++;
    }

    if (cueLines.length > 0) {
      cleanedBlocks.push(cueLines.join(" "));
    }
  }

  return cleanedBlocks.join("\n\n").trim();
}

export type TextMetrics = {
  chars: number;
  words: number;
  estimatedChunks: number;
  isSrt: boolean;
};

export function estimateTextMetrics(text: string): TextMetrics {
  const trimmed = text.trim();
  if (!trimmed) {
    return { chars: 0, words: 0, estimatedChunks: 0, isSrt: false };
  }
  const chars = trimmed.length;
  const cjkMatches = trimmed.match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/g);
  const nonCjkWords = trimmed
    .replace(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
  const words = nonCjkWords + (cjkMatches ? cjkMatches.length : 0);
  const estimatedChunks = Math.max(1, Math.ceil(chars / 1200));
  return {
    chars,
    words,
    estimatedChunks,
    isSrt: isSrtContent(trimmed),
  };
}

export type PipelineStep = {
  key: Exclude<JobStatus, "failed">;
  label: string;
};

export const PIPELINE_STEPS: readonly PipelineStep[] = [
  { key: "queued", label: "Queue" },
  { key: "parsing", label: "Parse" },
  { key: "translating", label: "Translate" },
  { key: "preparing_tts", label: "Narrate" },
  { key: "generating_audio", label: "Synthesize" },
  { key: "merging", label: "Master" },
  { key: "completed", label: "Ready" },
];

export type StepVisualState = "done" | "active" | "upcoming" | "error";

export function stepVisualState(
  jobStatus: JobStatus,
  jobStage: JobStatus,
  stepKey: Exclude<JobStatus, "failed">,
): StepVisualState {
  if (jobStatus === "completed") {
    return "done";
  }
  const order = PIPELINE_STEPS.map((s) => s.key);
  const activeStage = jobStatus === "failed" ? jobStage : jobStatus;
  const activeIdx = order.indexOf(activeStage === "failed" ? "queued" : activeStage);
  const stepIdx = order.indexOf(stepKey);

  if (jobStatus === "failed") {
    if (stepIdx < activeIdx) {
      return "done";
    }
    if (stepIdx === activeIdx) {
      return "error";
    }
    return "upcoming";
  }

  if (stepIdx < activeIdx) {
    return "done";
  }
  if (stepIdx === activeIdx) {
    return "active";
  }
  return "upcoming";
}

export function computeProgressPercent(
  status: JobStatus,
  chunkCurrent: number,
  chunkTotal: number,
): number {
  const ratio =
    chunkTotal > 0 ? Math.min(1, Math.max(0, chunkCurrent / chunkTotal)) : 0;
  switch (status) {
    case "queued":
      return 6;
    case "parsing":
      return 14;
    case "translating":
      return Math.round(18 + ratio * 34);
    case "preparing_tts":
      return 56;
    case "generating_audio":
      return Math.round(60 + ratio * 30);
    case "merging":
      return 94;
    case "completed":
      return 100;
    case "failed":
      return chunkTotal > 0 ? Math.max(10, Math.round(ratio * 85)) : 10;
  }
}
