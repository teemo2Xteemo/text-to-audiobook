import { useEffect, useMemo, useState, type FormEvent } from "react";

import type { Capabilities, CreateJobInput, OutputFormat, Voice } from "../api/types";
import { AUTO_SOURCE_LANGUAGE, SPEED_DEFAULT, SPEED_MAX, SPEED_MIN } from "../lib/constants";
import { envDefaultSourceLanguage, envDefaultTargetLanguage } from "../lib/env";
import {
  canGenerate,
  languageLabel,
  resolveSourceDefault,
  resolveTargetDefault,
  voicesForTarget,
} from "../lib/jobUi";

type JobFormProps = {
  capabilities: Capabilities;
  disabled: boolean;
  onSubmit: (input: CreateJobInput) => void;
};

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

  const targetVoices = useMemo(
    () => voicesForTarget(capabilities.voices, targetLanguage),
    [capabilities.voices, targetLanguage],
  );

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
    }
  }

  function handleFileChange(next: File | null) {
    setFile(next);
    if (next) {
      setText("");
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
    <form className="panel" onSubmit={handleSubmit}>
      <fieldset disabled={disabled}>
        <legend>Create job</legend>
        <div className="field-row">
          <label htmlFor="source-language">Source language</label>
          <select
            id="source-language"
            value={sourceLanguage}
            onChange={(event) => setSourceLanguage(event.target.value)}
          >
            <option value={AUTO_SOURCE_LANGUAGE}>{languageLabel(AUTO_SOURCE_LANGUAGE)}</option>
            {capabilities.languages.map((code) => (
              <option key={code} value={code}>
                {languageLabel(code)}
              </option>
            ))}
          </select>
        </div>
        <div className="field-row">
          <label htmlFor="target-language">Target language</label>
          <select
            id="target-language"
            value={targetLanguage}
            onChange={(event) => setTargetLanguage(event.target.value)}
          >
            {capabilities.languages.map((code) => (
              <option key={code} value={code}>
                {languageLabel(code)}
              </option>
            ))}
          </select>
        </div>
        <div className="field-row">
          <label htmlFor="voice">Voice</label>
          <select
            id="voice"
            value={voiceId}
            onChange={(event) => setVoiceId(event.target.value)}
          >
            {targetVoices.map((voice: Voice) => (
              <option key={voice.id} value={voice.id}>
                {voice.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field-row">
          <label htmlFor="speed">Speed</label>
          <input
            id="speed"
            type="number"
            min={SPEED_MIN}
            max={SPEED_MAX}
            step={0.1}
            value={speed}
            onChange={(event) => setSpeed(Number(event.target.value))}
          />
        </div>
        <div className="field-row">
          <label htmlFor="output-format">Output format</label>
          <select
            id="output-format"
            value={outputFormat}
            onChange={(event) => setOutputFormat(event.target.value as OutputFormat)}
          >
            <option value="mp3">mp3</option>
            <option value="wav">wav</option>
          </select>
        </div>
        <div className="field-row">
          <label htmlFor="story-text">Paste story</label>
          <textarea
            id="story-text"
            rows={8}
            value={text}
            onChange={(event) => handleTextChange(event.target.value)}
          />
        </div>
        <div className="field-row">
          <label htmlFor="story-file">Or upload a .txt file</label>
          <input
            id="story-file"
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
          />
        </div>
        <button type="submit" disabled={!ready}>
          Generate
        </button>
      </fieldset>
    </form>
  );
}
