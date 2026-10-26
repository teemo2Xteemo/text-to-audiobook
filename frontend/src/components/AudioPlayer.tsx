import { useEffect, useState } from "react";

type AudioPlayerProps = {
  src: string;
  subtitlesSrc?: string | null;
};

export function AudioPlayer({ src, subtitlesSrc }: AudioPlayerProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  const resolvedSubtitlesSrc =
    subtitlesSrc !== undefined
      ? subtitlesSrc
      : src.endsWith("/audio")
        ? src.replace(/\/audio$/, "/subtitles")
        : null;

  return (
    <section className="panel audio-deck">
      <div className="panel-header">
        <div>
          <span className="section-eyebrow">Step 3 · Master Output</span>
          <h2>Audiobook</h2>
        </div>
        <span className="ready-badge">
          <span className="equalizer-bars" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
          </span>
          Master Ready
        </span>
      </div>

      <div className="audio-player-box">
        <audio
          controls
          src={src}
          aria-label="Generated audiobook"
          onError={() => setFailed(true)}
        />
      </div>

      <div className="audio-actions">
        <a className="btn-download" href={src} download>
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
              d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"
            />
          </svg>
          Download Audio File
        </a>
        {resolvedSubtitlesSrc ? (
          <a className="btn-download" href={resolvedSubtitlesSrc} download>
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
                d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"
              />
            </svg>
            Download Subtitles (.srt)
          </a>
        ) : null}
      </div>

      {failed ? (
        <p role="alert" className="alert-banner alert-error">
          The browser could not play this audio file.
        </p>
      ) : null}
    </section>
  );
}
