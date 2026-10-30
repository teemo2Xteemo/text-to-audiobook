import { AUTO_SOURCE_LANGUAGE, SPEED_MAX, SPEED_MIN } from "./constants";
import type { JobMode, JobStatus, Voice } from "../api/types";

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
  mode?: JobMode,
): string {
  switch (status) {
    case "queued":
      return "Waiting in queue";
    case "parsing":
      return "Reading story";
    case "translating":
      if (mode === "narrate") {
        return chunkTotal > 0 ? `Preparing text ${chunkCurrent}/${chunkTotal}` : "Preparing text";
      }
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
const SRT_ASS_TAG_RE = /\{[^}]*\}/g;
const SRT_SOUND_EFFECT_RE = /^\s*(?:\[[^\]]+\]|\([^)]+\)|[♪♫][^♪♫]*[♪♫]|\*+[^*]+\*+)\s*$/;
const SRT_INLINE_SOUND_EFFECT_RE =
  /\[(?:Music|Applause|Laughter|Sighs|Gasps|Cheering|Groans|Crying)\]|\((?:applause|cheering|laughter|sighs|music)\)|[♪♫]/gi;

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
      if (SRT_SOUND_EFFECT_RE.test(line)) {
        i++;
        continue;
      }
      const cleaned = line
        .replace(/[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f]/g, "")
        .replace(SRT_HTML_TAG_RE, "")
        .replace(SRT_ASS_TAG_RE, "")
        .replace(SRT_INLINE_SOUND_EFFECT_RE, "")
        .replace(/^\s*[-–—]\s*/, "")
        .replace(/\s+[-–—]\s*/g, " ")
        .replace(/\s+/g, " ")
        .trim();
      if (cleaned) {
        cueLines.push(cleaned);
      }
      i++;
    }

    if (cueLines.length > 0) {
      const blockText = cueLines.join(" ");
      const merged =
        cleanedBlocks.length > 0
          ? mergeRollingCue(cleanedBlocks[cleanedBlocks.length - 1], blockText)
          : null;
      if (merged === null) {
        cleanedBlocks.push(blockText);
      } else {
        cleanedBlocks[cleanedBlocks.length - 1] = merged;
      }
    }
  }

  return cleanedBlocks.join("\n\n").trim();
}

const MIN_ROLLING_OVERLAP_WORDS = 2;

// Mirrors backend parse._merge_rolling_cue: collapse progressive/rolling captions.
function mergeRollingCue(previous: string, current: string): string | null {
  const prevWords = previous.split(/\s+/);
  const curWords = current.split(/\s+/);
  const sameWords = (a: string[], b: string[]) =>
    a.length === b.length && a.every((word, idx) => word === b[idx]);
  if (sameWords(curWords.slice(0, prevWords.length), prevWords)) {
    return current;
  }
  if (
    curWords.length >= MIN_ROLLING_OVERLAP_WORDS &&
    curWords.length <= prevWords.length &&
    sameWords(prevWords.slice(-curWords.length), curWords)
  ) {
    return previous;
  }
  for (
    let overlap = Math.min(prevWords.length, curWords.length) - 1;
    overlap >= MIN_ROLLING_OVERLAP_WORDS;
    overlap--
  ) {
    if (sameWords(prevWords.slice(-overlap), curWords.slice(0, overlap))) {
      return [...prevWords, ...curWords.slice(overlap)].join(" ");
    }
  }
  return null;
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

export function pipelineStepsForMode(mode?: JobMode): readonly PipelineStep[] {
  return [
    { key: "queued", label: "Queue" },
    { key: "parsing", label: "Parse" },
    { key: "translating", label: mode === "narrate" ? "Prepare" : "Translate" },
    { key: "preparing_tts", label: "Narrate" },
    { key: "generating_audio", label: "Synthesize" },
    { key: "merging", label: "Master" },
    { key: "completed", label: "Ready" },
  ];
}

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

export type FriendlyErrorResult = {
  summary: string;
  details?: string;
};

const ERROR_SUMMARY_MAP: Record<string, string> = {
  PROVIDER_CONFIGURATION_ERROR:
    "Service configuration issue. The service encountered a configuration error.",
  UNSUPPORTED_LANGUAGE: "This language or voice is currently not supported.",
  TIMEOUT: "The request timed out. Please try again with shorter text.",
  PROVIDER_RATE_LIMIT: "The service is temporarily busy. Please try again shortly.",
  TTS_FAILED: "Speech synthesis failed. Please try again or choose a different voice.",
  TRANSLATION_FAILED: "Translation failed. Please try again or select a different engine.",
  INVALID_INPUT: "The provided input is invalid. Please check your text or file.",
  STORAGE_FAILED: "Failed to store or retrieve audio file.",
};

export function friendlyError(
  errorType: string | null | undefined,
  message: string | null | undefined,
): FriendlyErrorResult {
  const normType = (errorType ?? "").trim();
  const normMessage = (message ?? "").trim();

  const summary =
    ERROR_SUMMARY_MAP[normType] ||
    "An unexpected error occurred while processing the request.";

  let details: string | undefined;
  if (normType && normMessage) {
    details = `${normType}: ${normMessage}`;
  } else if (normType) {
    details = normType;
  } else if (normMessage) {
    details = normMessage;
  }

  return { summary, details };
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "0s";
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  if (minutes > 0) {
    return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
  }
  return `${secs}s`;
}
