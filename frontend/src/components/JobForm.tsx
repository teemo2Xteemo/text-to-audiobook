import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";

import type { Capabilities, CreateJobInput, OutputFormat, Voice } from "../api/types";
import { AUTO_SOURCE_LANGUAGE, SPEED_DEFAULT, SPEED_MAX, SPEED_MIN } from "../lib/constants";
import { envDefaultSourceLanguage, envDefaultTargetLanguage } from "../lib/env";
import {
  canGenerate,
  estimateTextMetrics,
  languageDisplayName,
  languageLabel,
  resolveSourceDefault,
  resolveTargetDefault,
  stripSrtToText,
  voiceDisplayParts,
  voicesForTarget,
} from "../lib/jobUi";
import { StudioSelect, type StudioSelectOption } from "./StudioSelect";

type JobFormProps = {
  capabilities: Capabilities;
  disabled: boolean;
  onSubmit: (input: CreateJobInput) => void;
};

const SPEED_PRESETS = [0.75, 1.0, 1.25, 1.5] as const;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  return `${(kb / 1024).toFixed(2)} MB`;
}

export function JobForm({ capabilities, disabled, onSubmit }: JobFormProps) {
  const sourceDefault = resolveSourceDefault(
    envDefaultSourceLanguage(),
    capabilities.languages,
  );
  const targetDefault = resolveTargetDefault(capabilities.languages, envDefaultTargetLanguage());

  const [sourceLanguage, setSourceLanguage] = useState(sourceDefault);
  const [targetLanguage, setTargetLanguage] = useState(targetDefault);
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(SPEED_DEFAULT);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("mp3");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploadedDisplayName, setUploadedDisplayName] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const targetVoices = useMemo(
    () => voicesForTarget(capabilities.voices, targetLanguage),
    [capabilities.voices, targetLanguage],
  );

  const sourceOptions = useMemo<StudioSelectOption[]>(
    () => [
      {
        value: AUTO_SOURCE_LANGUAGE,
        label: languageLabel(AUTO_SOURCE_LANGUAGE),
        displayLabel: languageDisplayName(AUTO_SOURCE_LANGUAGE),
        badge: "AUTO",
      },
      ...capabilities.languages.map((code) => ({
        value: code,
        label: languageLabel(code),
        displayLabel: languageDisplayName(code),
        badge: code.toUpperCase(),
      })),
    ],
    [capabilities.languages],
  );

  const targetOptions = useMemo<StudioSelectOption[]>(
    () =>
      capabilities.languages.map((code) => ({
        value: code,
        label: languageLabel(code),
        displayLabel: languageDisplayName(code),
        badge: code.toUpperCase(),
      })),
    [capabilities.languages],
  );

  const voiceOptions = useMemo<StudioSelectOption[]>(
    () =>
      targetVoices.map((voice: Voice) => {
        const parts = voiceDisplayParts(voice.label, voice.id);
        return {
          value: voice.id,
          label: voice.label,
          displayLabel: parts.title,
          badge: voice.language.toUpperCase(),
          description: parts.subtitle,
        };
      }),
    [targetVoices],
  );

  const formatOptions = useMemo<StudioSelectOption[]>(
    () => [
      {
        value: "mp3",
        label: "mp3",
        displayLabel: "MP3 Audio",
        badge: "128 KBPS",
        description: "Standard stream",
      },
      {
        value: "wav",
        label: "wav",
        displayLabel: "WAV Lossless",
        badge: "PCM WAV",
        description: "Studio master",
      },
    ],
    [],
  );

  const metrics = useMemo(() => estimateTextMetrics(text), [text]);

  useEffect(() => {
    setVoiceId((current) => {
      if (current && targetVoices.some((voice) => voice.id === current)) {
        return current;
      }
      return targetVoices[0]?.id ?? "";
    });
  }, [targetVoices]);

  const ready = canGenerate({
    capabilitiesLoaded: true,
    targetLanguage,
    hasVoice: Boolean(voiceId),
    text,
    file,
    speed,
  });

  function handleTextChange(value: string) {
    setText(value);
    if (value.trim()) {
      setFile(null);
      setUploadedDisplayName(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function handleCleanSrt() {
    if (!text.trim()) {
      return;
    }
    setText(stripSrtToText(text));
  }

  async function processSelectedFile(next: File | null) {
    if (!next) {
      setFile(null);
      setUploadedDisplayName(null);
      return;
    }
    setText("");
    const lowerName = next.name.toLowerCase();
    if (lowerName.endsWith(".srt")) {
      try {
        const raw = await next.text();
        const cleaned = stripSrtToText(raw) || raw;
        const txtName = next.name.replace(/\.srt$/i, ".txt");
        const converted = new File([cleaned], txtName, { type: "text/plain" });
        setFile(converted);
        setUploadedDisplayName(`${next.name} (SRT cleaned)`);
        return;
      } catch {
        // Fallback to original file if reading fails
      }
    }
    setFile(next);
    setUploadedDisplayName(next.name);
  }

  function handleFileChange(next: File | null) {
    void processSelectedFile(next);
  }

  function handleRemoveFile() {
    setFile(null);
    setUploadedDisplayName(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!disabled) {
      setDragActive(true);
    }
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    if (disabled) {
      return;
    }
    const dropped = event.dataTransfer.files?.[0] ?? null;
    if (dropped) {
      handleFileChange(dropped);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || disabled) {
      return;
    }
    const input: CreateJobInput = {
      source_language: sourceLanguage,
      target_language: targetLanguage,
      voice: voiceId || undefined,
      speed,
      output_format: outputFormat,
    };
    if (file) {
      input.file = file;
    } else {
      input.text = text;
    }
    onSubmit(input);
  }

  return (
    <form className="panel studio-form" onSubmit={handleSubmit}>
      <fieldset disabled={disabled}>
        <div className="panel-header">
          <div>
            <span className="section-eyebrow">Step 1 · Configure &amp; Input</span>
            <legend>Create job</legend>
          </div>
          <span className="capability-pill">
            {capabilities.languages.length} languages · {capabilities.voices.length} voices
          </span>
        </div>

        {/* Translation Route */}
        <div className="form-section">
          <div className="section-label-row">
            <span className="section-title">
              <svg
                className="icon-inline"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="m5 8 6 6m-7 0 6-6 2-3M2 5h12M7 2h1m14 20-5-10-5 10m2-4h6"
                />
              </svg>
              Translation Route
            </span>
            <span className="section-hint">Auto-detect or lock source language</span>
          </div>

          <div className="language-route-grid">
            <StudioSelect
              id="source-language"
              label="Source language"
              value={sourceLanguage}
              options={sourceOptions}
              disabled={disabled}
              onChange={setSourceLanguage}
              icon={
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <circle cx="12" cy="12" r="9" />
                  <path
                    strokeLinecap="round"
                    d="M3.6 9h16.8M3.6 15h16.8M12 3a14.5 14.5 0 0 0 0 18M12 3a14.5 14.5 0 0 1 0 18"
                  />
                </svg>
              }
            />

            <div
              className={`route-connector ${
                sourceLanguage !== AUTO_SOURCE_LANGUAGE ? "route-connector-swappable" : ""
              }`}
              title={
                sourceLanguage !== AUTO_SOURCE_LANGUAGE
                  ? "Click to swap source and target languages"
                  : "Translation direction"
              }
              onClick={() => {
                if (!disabled && sourceLanguage !== AUTO_SOURCE_LANGUAGE) {
                  const prevSource = sourceLanguage;
                  setSourceLanguage(targetLanguage);
                  setTargetLanguage(prevSource);
                }
              }}
              aria-hidden="true"
            >
              <span className="route-connector-circle">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  className="route-arrow-icon"
                >
                  {sourceLanguage !== AUTO_SOURCE_LANGUAGE ? (
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M7 16V4m0 0L3 8m4-4 4 4m6 0v12m0 0 4-4m-4 4-4-4"
                    />
                  ) : (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14m-6-6 6 6-6 6" />
                  )}
                </svg>
              </span>
            </div>

            <StudioSelect
              id="target-language"
              label="Target language"
              value={targetLanguage}
              options={targetOptions}
              disabled={disabled}
              onChange={setTargetLanguage}
              icon={
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m5 8 6 6m-7 0 6-6 2-3M2 5h12M7 2h1m14 20-5-10-5 10m2-4h6"
                  />
                </svg>
              }
            />
          </div>
        </div>

        {/* Voice & Mastering */}
        <div className="form-section">
          <div className="section-label-row">
            <span className="section-title">
              <svg
                className="icon-inline"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"
                />
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
              </svg>
              Voice &amp; Audio Mastering
            </span>
            <span className="section-hint">
              {targetVoices.length} {targetVoices.length === 1 ? "voice" : "voices"} for{" "}
              {targetLanguage}
            </span>
          </div>

          <div className="voice-mastering-grid">
            <div className="field-voice">
              <StudioSelect
                id="voice"
                label="Voice"
                value={voiceId}
                options={voiceOptions}
                disabled={disabled}
                onChange={setVoiceId}
                placeholder="No voice available"
                icon={
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3"
                    />
                  </svg>
                }
              />
            </div>

            <div className="field-row field-speed">
              <div className="label-with-value">
                <label htmlFor="speed">Speed</label>
                <span className="mono-badge">{Number.isFinite(speed) ? `${speed}x` : "—"}</span>
              </div>
              <div className="speed-control-card">
                <input
                  id="speed"
                  type="number"
                  min={SPEED_MIN}
                  max={SPEED_MAX}
                  step={0.1}
                  value={speed}
                  onChange={(event) => setSpeed(Number(event.target.value))}
                />
                <div className="speed-presets" role="group" aria-label="Narration speed presets">
                  {SPEED_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      className={`chip-btn ${speed === preset ? "chip-btn-active" : ""}`}
                      onClick={() => setSpeed(preset)}
                    >
                      {preset}x
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="field-format">
              <StudioSelect
                id="output-format"
                label="Output format"
                value={outputFormat}
                options={formatOptions}
                disabled={disabled}
                onChange={(next) => setOutputFormat(next as OutputFormat)}
                icon={
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm12-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
                    />
                  </svg>
                }
              />
            </div>
          </div>
        </div>

        {/* Story / SRT Input */}
        <div className="form-section">
          <div className="section-label-row">
            <span className="section-title">
              <svg
                className="icon-inline"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
                />
                <path strokeLinecap="round" strokeLinejoin="round" d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
              </svg>
              Source Script (Story Text or SRT Subtitles)
            </span>
            <div className="editor-metrics" aria-live="polite">
              {metrics.isSrt ? (
                <span className="srt-badge">SRT Timecodes Detected</span>
              ) : null}
              <span className="metric-item">{metrics.chars.toLocaleString()} chars</span>
              <span className="metric-sep">·</span>
              <span className="metric-item">{metrics.words.toLocaleString()} words</span>
              {metrics.estimatedChunks > 0 ? (
                <>
                  <span className="metric-sep">·</span>
                  <span className="metric-item">~{metrics.estimatedChunks} chunks</span>
                </>
              ) : null}
            </div>
          </div>

          <div className="field-row editor-wrapper">
            <div className="editor-toolbar">
              <label htmlFor="story-text">Paste story</label>
              <div className="editor-actions">
                {metrics.isSrt ? (
                  <button
                    type="button"
                    className="btn-ghost btn-xs"
                    onClick={handleCleanSrt}
                  >
                    Strip SRT timestamps
                  </button>
                ) : null}
                {text.length > 0 ? (
                  <button
                    type="button"
                    className="btn-ghost btn-xs"
                    onClick={() => handleTextChange("")}
                  >
                    Clear text
                  </button>
                ) : null}
              </div>
            </div>
            <textarea
              id="story-text"
              rows={8}
              placeholder="Paste your story chapter, article, or .srt subtitle content here..."
              value={text}
              onChange={(event) => handleTextChange(event.target.value)}
            />
          </div>

          <div className="divider-or" aria-hidden="true">
            <span>OR IMPORT SCRIPT FILE</span>
          </div>

          <div
            className={`field-row upload-dropzone ${dragActive ? "upload-dropzone-active" : ""} ${
              file ? "upload-dropzone-has-file" : ""
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div className="upload-header">
              <div className="upload-title-group">
                <svg
                  className="upload-icon"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"
                  />
                </svg>
                <div>
                  <label htmlFor="story-file">Or upload a .txt file</label>
                  <p className="upload-subtext">
                    Drag &amp; drop or browse · Supports <code>.txt</code> and <code>.srt</code>{" "}
                    subtitle scripts (UTF-8)
                  </p>
                </div>
              </div>
              {file ? (
                <button
                  type="button"
                  className="btn-ghost btn-xs"
                  onClick={handleRemoveFile}
                >
                  Remove file
                </button>
              ) : null}
            </div>

            <input
              ref={fileInputRef}
              id="story-file"
              type="file"
              accept=".txt,.srt,text/plain"
              onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            />

            {file ? (
              <div className="selected-file-pill" aria-live="polite">
                <span className="file-pill-name">{uploadedDisplayName ?? file.name}</span>
                <span className="mono-badge">{formatFileSize(file.size)}</span>
              </div>
            ) : null}
          </div>
        </div>

        <button type="submit" className="btn-generate" disabled={!ready}>
          <svg
            className="btn-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2 10v3m4-7v11m4-14v18m4-13v7m4-10v13m4-8v3"
            />
          </svg>
          Generate
        </button>
      </fieldset>
    </form>
  );
}
