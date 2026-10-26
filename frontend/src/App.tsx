import { useEffect, useState } from "react";

import { AudioPlayer } from "./components/AudioPlayer";
import { JobForm } from "./components/JobForm";
import { JobProgress } from "./components/JobProgress";
import { useCapabilities } from "./hooks/useCapabilities";
import { jobFromView, useJob } from "./hooks/useJob";
import { apiBaseUrl } from "./lib/env";
import { playerSrc } from "./lib/jobUi";

type ThemeMode = "dark" | "light";

export default function App() {
  const capabilities = useCapabilities();
  const job = useJob();
  const busy = job.status === "loading";
  const currentJob = jobFromView(job);
  const audioUrl = job.status === "success" ? job.job.audio_url : null;
  const subtitlesUrl = job.status === "success" ? job.job.subtitles_url : null;

  const [theme, setTheme] = useState<ThemeMode>("dark");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  return (
    <div className="page">
      <header className="studio-header">
        <div className="brand-block">
          <div className="brand-badge-row">
            <span className="brand-mark" aria-hidden="true">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="brand-icon"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M2 10v3m4-7v11m4-14v18m4-13v7m4-10v13m4-8v3"
                />
              </svg>
              Multilingual Narration Studio
            </span>
            <span className="format-pill">TXT &amp; SRT → MP3 / WAV</span>
          </div>
          <h1>Story to audiobook</h1>
          <p className="lede">
            Paste or upload a story or <code>.srt</code> subtitle script, choose languages from the
            live API, and generate studio-normalized narration audio.
          </p>
        </div>

        <div className="header-controls">
          <span
            className={`api-status-badge api-status-${capabilities.status}`}
            aria-live="polite"
          >
            <span className="api-status-dot" aria-hidden="true" />
            {capabilities.status === "success"
              ? "API Connected"
              : capabilities.status === "error"
                ? "API Error"
                : "Connecting…"}
          </span>

          <button
            type="button"
            className="btn-theme-toggle"
            onClick={toggleTheme}
            aria-label="Toggle color theme"
          >
            {theme === "dark" ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                className="theme-icon"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="4" />
                <path
                  strokeLinecap="round"
                  d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41"
                />
              </svg>
            ) : (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                className="theme-icon"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"
                />
              </svg>
            )}
            <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
          </button>
        </div>
      </header>

      <main className="studio-grid">
        <div className="studio-main">
          {capabilities.status === "loading" || capabilities.status === "idle" ? (
            <div className="panel loading-skeleton" aria-busy="true">
              <div className="spinner-ring" aria-hidden="true" />
              <p>Loading languages and voices…</p>
            </div>
          ) : null}

          {capabilities.status === "error" ? (
            <p role="alert" className="alert-banner alert-error">
              {capabilities.error.error_type}: {capabilities.error.message}
            </p>
          ) : null}

          {capabilities.status === "success" ? (
            <JobForm
              capabilities={capabilities.data}
              disabled={busy}
              onSubmit={(input) => void job.submit(input)}
            />
          ) : null}
        </div>

        <aside className="studio-sidebar" aria-label="Pipeline monitor and audio output">
          {currentJob ? <JobProgress job={currentJob} /> : null}

          {job.status === "error" && !job.job ? (
            <p role="alert" className="alert-banner alert-error">
              {job.error.error_type}: {job.error.message}
            </p>
          ) : null}

          {audioUrl ? (
            <AudioPlayer
              src={playerSrc(apiBaseUrl(), audioUrl)}
              subtitlesSrc={subtitlesUrl ? playerSrc(apiBaseUrl(), subtitlesUrl) : null}
            />
          ) : null}

          <section className="panel workflow-card" aria-label="Studio pipeline overview">
            <span className="section-eyebrow">Studio Architecture</span>
            <h2>How the Pipeline Works</h2>
            <ol className="workflow-list">
              <li>
                <span className="workflow-num">01</span>
                <div>
                  <strong>Parse &amp; Chunk Script</strong>
                  <p className="muted">
                    Normalizes UTF-8 story text or <code>.srt</code> subtitles and splits long
                    narratives at safe sentence boundaries.
                  </p>
                </div>
              </li>
              <li>
                <span className="workflow-num">02</span>
                <div>
                  <strong>Neural Translation</strong>
                  <p className="muted">
                    Translates chunk-by-chunk with checkpoint caching and automatic retry on
                    transient errors.
                  </p>
                </div>
              </li>
              <li>
                <span className="workflow-num">03</span>
                <div>
                  <strong>Narration &amp; Voice Synthesis</strong>
                  <p className="muted">
                    Prepares spoken-friendly punctuation and synthesizes target-language speech at
                    your selected tempo.
                  </p>
                </div>
              </li>
              <li>
                <span className="workflow-num">04</span>
                <div>
                  <strong>FFmpeg Loudness Mastering</strong>
                  <p className="muted">
                    Normalizes audio levels and merges all chunks into a seamless MP3 or lossless
                    WAV audiobook.
                  </p>
                </div>
              </li>
            </ol>
          </section>
        </aside>
      </main>
    </div>
  );
}
