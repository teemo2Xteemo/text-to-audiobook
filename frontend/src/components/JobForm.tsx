import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";

import { analyze, previewTts, toApiError } from "../api/client";
import type {
  AnalyzeResponse,
  Capabilities,
  CreateJobInput,
  JobMode,
  OutputFormat,
  Voice,
} from "../api/types";
import {
  ALLOWED_FILE_EXTENSIONS,
  AUTO_SOURCE_LANGUAGE,
  MAX_FILE_SIZE_BYTES,
  SPEED_DEFAULT,
  SPEED_MAX,
  SPEED_MIN,
} from "../lib/constants";
import { envDefaultSourceLanguage, envDefaultTargetLanguage } from "../lib/env";
import {
  canGenerate,
  estimateTextMetrics,
  formatDuration,
  friendlyError,
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

const SAMPLE_PREVIEWS: Record<string, string> = {
  vi: "Xin chào, đây là giọng đọc thử nghiệm cho sách nói của bạn.",
  en: "Hello, this is a sample preview of this voice for your audiobook.",
  ja: "こんにちは、これはオーディオブックの音声プレビューです。",
  zh: "你好，这是您的有声书语音试听示例。",
  ko: "안녕하세요, 오디오북 음성 미리듣기 샘플입니다.",
  fr: "Bonjour, ceci est un aperçu audio pour votre livre audio.",
  de: "Hallo, dies ist eine Hörprobe für Ihr Hörbuch.",
  es: "Hola, esta es una muestra de audio para su audiolibro.",
  ru: "Здравствуйте, это образец озвучивания для вашей аудиокниги.",
};

function getVoicePreviewSample(lang: string, fallbackText: string): string {
  const prefix = lang.split("-")[0]?.toLowerCase();
  return SAMPLE_PREVIEWS[prefix] || fallbackText || "Hello, this is a voice sample preview.";
}

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
  const [mode, setMode] = useState<JobMode>("translate");

  const narrateLanguages = useMemo(
    () =>
      capabilities.narrate_languages && capabilities.narrate_languages.length > 0
        ? capabilities.narrate_languages
        : capabilities.languages,
    [capabilities.narrate_languages, capabilities.languages],
  );

  const translateLanguages = capabilities.languages;

  const sourceDefault = resolveSourceDefault(
    envDefaultSourceLanguage(),
    capabilities.languages,
  );
  const targetDefault = resolveTargetDefault(capabilities.languages, envDefaultTargetLanguage());

  const defaultProfileId = useMemo(() => {
    const profiles = capabilities.translation_profiles ?? [];
    const def = profiles.find((p) => p.is_default);
    return def ? def.id : profiles[0]?.id ?? "";
  }, [capabilities.translation_profiles]);

  const [sourceLanguage, setSourceLanguage] = useState(sourceDefault);
  const [targetLanguage, setTargetLanguage] = useState(targetDefault);
  const [translationProfileId, setTranslationProfileId] = useState(defaultProfileId);
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(SPEED_DEFAULT);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("mp3");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploadedDisplayName, setUploadedDisplayName] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [confirmedCandidate, setConfirmedCandidate] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);

  function handleModeChange(nextMode: JobMode) {
    if (nextMode === mode) return;
    if (nextMode === "narrate") {
      if (sourceLanguage !== AUTO_SOURCE_LANGUAGE && narrateLanguages.includes(sourceLanguage)) {
        // Retain sourceLanguage
      } else if (targetLanguage && narrateLanguages.includes(targetLanguage)) {
        setSourceLanguage(targetLanguage);
      } else {
        setSourceLanguage(narrateLanguages[0] ?? "");
      }
    } else {
      if (translateLanguages.includes(sourceLanguage)) {
        // Retain sourceLanguage
      } else {
        setSourceLanguage(sourceDefault);
      }
    }
    setMode(nextMode);
  }

  const activeVoiceLanguage =
    mode === "narrate"
      ? sourceLanguage === AUTO_SOURCE_LANGUAGE
        ? analysis?.language || AUTO_SOURCE_LANGUAGE
        : sourceLanguage
      : targetLanguage;
  const targetVoices = useMemo(
    () => voicesForTarget(capabilities.voices, activeVoiceLanguage),
    [capabilities.voices, activeVoiceLanguage],
  );

  const sourceOptions = useMemo<StudioSelectOption[]>(() => {
    const list = mode === "narrate" ? narrateLanguages : translateLanguages;
    return [
      {
        value: AUTO_SOURCE_LANGUAGE,
        label: languageLabel(AUTO_SOURCE_LANGUAGE),
        displayLabel: languageDisplayName(AUTO_SOURCE_LANGUAGE),
        badge: "AUTO",
      },
      ...list.map((code) => ({
        value: code,
        label: languageLabel(code),
        displayLabel: languageDisplayName(code),
        badge: code.toUpperCase(),
      })),
    ];
  }, [mode, narrateLanguages, translateLanguages]);

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

  const profileOptions = useMemo<StudioSelectOption[]>(
    () =>
      (capabilities.translation_profiles ?? []).map((p) => ({
        value: p.id,
        label: p.label,
        displayLabel: p.label,
        badge: p.badge,
        description: p.description,
      })),
    [capabilities.translation_profiles],
  );

  const metrics = useMemo(() => estimateTextMetrics(text), [text]);

  useEffect(() => {
    setTranslationProfileId((current) => {
      const profiles = capabilities.translation_profiles ?? [];
      if (current && profiles.some((p) => p.id === current)) {
        return current;
      }
      const def = profiles.find((p) => p.is_default);
      return def ? def.id : profiles[0]?.id ?? "";
    });
  }, [capabilities.translation_profiles]);

  useEffect(() => {
    setVoiceId((current) => {
      if (current && targetVoices.some((voice) => voice.id === current)) {
        return current;
      }
      return targetVoices[0]?.id ?? "";
    });
  }, [targetVoices]);

  useEffect(() => {
    if (file) {
      let active = true;
      setIsAnalyzing(true);
      analyze({ file })
        .then((res) => {
          if (active) {
            setAnalysis(res);
            setIsAnalyzing(false);
          }
        })
        .catch(() => {
          if (active) {
            setIsAnalyzing(false);
          }
        });
      return () => {
        active = false;
      };
    }

    const trimmed = text.trim();
    if (!trimmed) {
      setAnalysis(null);
      setIsAnalyzing(false);
      return;
    }

    let active = true;
    const timer = setTimeout(() => {
      setIsAnalyzing(true);
      const sampleText = trimmed.length > 20000 ? trimmed.slice(0, 20000) : trimmed;
      analyze({ text: sampleText })
        .then((res) => {
          if (active) {
            if (trimmed.length > 20000) {
              const ratio = trimmed.length / sampleText.length;
              setAnalysis({
                ...res,
                duration_estimate_seconds: Math.round(res.duration_estimate_seconds * ratio * 10) / 10,
                process_time_estimate_seconds: Math.round(res.process_time_estimate_seconds * ratio * 10) / 10,
              });
            } else {
              setAnalysis(res);
            }
            setIsAnalyzing(false);
          }
        })
        .catch(() => {
          if (active) {
            setIsAnalyzing(false);
          }
        });
    }, 400);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [text, file]);

  const targetForCheck = mode === "narrate" ? sourceLanguage : targetLanguage;
  const ready = canGenerate({
    capabilitiesLoaded: true,
    targetLanguage: targetForCheck,
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
      setFileError(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function handleCleanSrt() {
    if (!text.trim()) {
      return;
    }
    const cleaned = stripSrtToText(text);
    setText(cleaned);
  }

  function validateFile(candidate: File): string | null {
    const lowerName = candidate.name.toLowerCase();
    const isAllowedExt = ALLOWED_FILE_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
    if (!isAllowedExt) {
      return "Only .txt, .srt, and .vtt files are supported.";
    }
    if (candidate.size > MAX_FILE_SIZE_BYTES) {
      return `File exceeds maximum allowed size of 2 MB (${formatFileSize(candidate.size)}).`;
    }
    return null;
  }

  function handleFileChange(next: File | null) {
    if (!next) {
      setFile(null);
      setUploadedDisplayName(null);
      setFileError(null);
      return;
    }
    const err = validateFile(next);
    if (err) {
      setFile(null);
      setUploadedDisplayName(null);
      setFileError(err);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return;
    }
    setFileError(null);
    setText("");
    setFile(next);
    setUploadedDisplayName(next.name);
  }

  function handleRemoveFile() {
    setFile(null);
    setUploadedDisplayName(null);
    setFileError(null);
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

  async function handleListenPreview() {
    if (!voiceId || previewLoading || previewPlaying) {
      return;
    }
    const sample = (
      mode === "narrate"
        ? (text.trim() || analysis?.preview_lines?.join(" ") || getVoicePreviewSample(activeVoiceLanguage, "Audio preview"))
        : getVoicePreviewSample(targetLanguage, "Audio preview")
    ).slice(0, 200);

    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const res = await previewTts({
        text: sample,
        voice: voiceId,
        speed,
      });
      const dataUrl = `data:${res.mime_type};base64,${res.audio_base64}`;
      if (previewAudioRef.current) {
        previewAudioRef.current.src = dataUrl;
        setPreviewPlaying(true);
        try {
          const playPromise = previewAudioRef.current.play?.();
          if (playPromise && typeof playPromise.catch === "function") {
            await playPromise.catch(() => setPreviewPlaying(false));
          }
        } catch {
          setPreviewPlaying(false);
        }
      }
    } catch (err) {
      setPreviewPlaying(false);
      const apiErr = toApiError(err);
      const { summary } = friendlyError(apiErr.error_type, apiErr.message);
      setPreviewError(summary);
    } finally {
      setPreviewLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || disabled) {
      return;
    }
    const input: CreateJobInput = {
      source_language: sourceLanguage,
      target_language: mode === "narrate" ? sourceLanguage : targetLanguage,
      voice: voiceId || undefined,
      speed,
      output_format: outputFormat,
      translation_profile_id:
        mode === "narrate" ? undefined : (translationProfileId || undefined),
      mode,
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

        {/* Mode Toggle */}
        <div className="mode-toggle-container">
          <div className="mode-toggle-group" role="tablist" aria-label="Job mode">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "translate"}
              className={`mode-toggle-btn ${mode === "translate" ? "mode-toggle-btn-active" : ""}`}
              onClick={() => handleModeChange("translate")}
            >
              Translate
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "narrate"}
              className={`mode-toggle-btn ${mode === "narrate" ? "mode-toggle-btn-active" : ""}`}
              onClick={() => handleModeChange("narrate")}
            >
              <span>Narrate</span>
              <span className="mode-pill-free">No model cost</span>
            </button>
          </div>
        </div>

        {/* Same-language Translate suggestion banner */}
        {mode === "translate" &&
          sourceLanguage !== AUTO_SOURCE_LANGUAGE &&
          sourceLanguage === targetLanguage && (
            <div className="same-language-banner" role="status">
              <div className="same-language-info">
                <span className="same-language-icon" aria-hidden="true">
                  💡
                </span>
                <span>Same language — switch to Narrate directly?</span>
              </div>
              <button
                type="button"
                className="btn-ghost btn-xs switch-narrate-btn"
                onClick={() => handleModeChange("narrate")}
              >
                Switch to Narrate
              </button>
            </div>
          )}

        {/* Translation Route / Narration Language */}
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
              {mode === "narrate" ? "Narration Language" : "Translation Route"}
            </span>
            <span className="section-hint">
              {mode === "narrate"
                ? "Direct speech synthesis without translation"
                : "Auto-detect or lock source language"}
            </span>
          </div>

          <div className={mode === "narrate" ? "language-route-single" : "language-route-grid"}>
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

            {mode === "translate" && (
              <>
                <button
                  type="button"
                  className={`route-connector ${
                    sourceLanguage !== AUTO_SOURCE_LANGUAGE ? "route-connector-swappable" : ""
                  }`}
                  title={
                    sourceLanguage !== AUTO_SOURCE_LANGUAGE
                      ? "Click to swap source and target languages"
                      : "Translation direction"
                  }
                  aria-label="Swap source and target languages"
                  disabled={disabled || sourceLanguage === AUTO_SOURCE_LANGUAGE}
                  onClick={() => {
                    if (!disabled && sourceLanguage !== AUTO_SOURCE_LANGUAGE) {
                      const prevSource = sourceLanguage;
                      setSourceLanguage(targetLanguage);
                      setTargetLanguage(prevSource);
                    }
                  }}
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
                </button>

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
              </>
            )}
          </div>

          {mode === "narrate" ? (
            <div className="narrate-mode-note">
              <span className="muted italic text-sm">No translation needed</span>
            </div>
          ) : (
            profileOptions.length > 0 && (
              <div className="translation-profile-row">
                <StudioSelect
                  id="translation-profile"
                  label="Translation engine"
                  value={translationProfileId}
                  options={profileOptions}
                  disabled={disabled}
                  onChange={setTranslationProfileId}
                  icon={
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"
                      />
                    </svg>
                  }
                />
              </div>
            )
          )}
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
              {activeVoiceLanguage}
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
                    title="Removes timing; output won't be synchronized"
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
                  <label htmlFor="story-file">Or upload a .txt or .srt file</label>
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
              accept=".txt,.srt,.vtt,text/plain,text/vtt"
              onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            />

            {fileError ? (
              <div
                className="upload-error-msg"
                role="alert"
                style={{
                  color: "var(--destructive, #dc2626)",
                  fontSize: "0.85rem",
                  marginTop: "0.5rem",
                }}
              >
                {fileError}
              </div>
            ) : null}

            {file ? (
              <div className="selected-file-pill" aria-live="polite">
                <span className="file-pill-name">{uploadedDisplayName ?? file.name}</span>
                <span className="mono-badge">{formatFileSize(file.size)}</span>
              </div>
            ) : null}
          </div>

          {/* Analysis & Preview Panel */}
          {(analysis || isAnalyzing) && (
            <div className="analysis-panel" aria-label="Text analysis summary">
              <div className="analysis-header">
                <span className="analysis-title">
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
                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.5L19 7.5V19a2 2 0 0 1-2 2Z"
                    />
                  </svg>
                  Script Analysis
                </span>
                {isAnalyzing ? <span className="analysis-loading muted">Analyzing…</span> : null}
              </div>

              {analysis && (
                <>
                  <div className="analysis-metrics-grid">
                    <div className="analysis-metric">
                      <span className="metric-label">Detected language</span>
                      <span className="metric-value">
                        {languageLabel(analysis.language)} ({Math.round(analysis.confidence * 100)}%)
                      </span>
                    </div>
                    <div className="analysis-metric">
                      <span className="metric-label">Detected encoding</span>
                      <span className="metric-value">{analysis.encoding.toUpperCase()}</span>
                    </div>
                    <div className="analysis-metric">
                      <span className="metric-label">Chapters</span>
                      <span className="metric-value">
                        {analysis.chapters_count} {analysis.chapters_count === 1 ? "chapter" : "chapters"}
                      </span>
                    </div>
                    <div className="analysis-metric">
                      <span className="metric-label">Est. audio duration</span>
                      <span className="metric-value">{formatDuration(analysis.duration_estimate_seconds)}</span>
                    </div>
                    <div className="analysis-metric">
                      <span className="metric-label">Est. processing time</span>
                      <span className="metric-value">~{Math.round(analysis.process_time_estimate_seconds)}s</span>
                    </div>
                  </div>

                  {(analysis.needs_confirmation || analysis.confidence < 0.7) && confirmedCandidate !== sourceLanguage && (
                    <div role="alert" className="confidence-warning-banner">
                      <div className="warning-text">
                        Low language detection confidence ({Math.round(analysis.confidence * 100)}%). Please confirm the language:
                      </div>
                      {analysis.candidates && analysis.candidates.length > 0 && (
                        <div className="candidate-language-chips">
                          {analysis.candidates.map((cand) => (
                            <button
                              key={cand.language}
                              type="button"
                              className="chip-btn candidate-chip"
                              onClick={() => {
                                setSourceLanguage(cand.language);
                                setConfirmedCandidate(cand.language);
                              }}
                            >
                              {languageDisplayName(cand.language)} ({Math.round(cand.confidence * 100)}%)
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  <div className="preview-listen-row">
                    <button
                      type="button"
                      className="btn-ghost btn-xs listen-preview-btn"
                      disabled={disabled || previewLoading || previewPlaying || !voiceId}
                      onClick={handleListenPreview}
                    >
                      <svg
                        className="btn-icon"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        aria-hidden="true"
                      >
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                        <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
                      </svg>
                      {previewLoading ? "Loading preview..." : previewPlaying ? "Playing..." : "Listen"}
                    </button>
                    {previewError && (
                      <span className="preview-error-text text-xs text-rose-500" role="alert">
                        {previewError}
                      </span>
                    )}
                    <audio
                      ref={previewAudioRef}
                      onEnded={() => setPreviewPlaying(false)}
                      onError={() => setPreviewPlaying(false)}
                      style={{ display: "none" }}
                      aria-hidden="true"
                    />
                  </div>
                </>
              )}
            </div>
          )}
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
