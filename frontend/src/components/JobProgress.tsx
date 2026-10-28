import type { JobStatusResponse } from "../api/types";
import {
  computeProgressPercent,
  friendlyError,
  pipelineStepsForMode,
  progressCopy,
  stepVisualState,
} from "../lib/jobUi";

type JobProgressProps = {
  job: JobStatusResponse;
};

export function JobProgress({ job }: JobProgressProps) {
  const copy = progressCopy(job.status, job.chunk_current, job.chunk_total, job.mode);
  const percent = computeProgressPercent(job.status, job.chunk_current, job.chunk_total);
  const steps = pipelineStepsForMode(job.mode);

  return (
    <section className="panel progress-panel" aria-live="polite">
      <div className="panel-header">
        <div>
          <span className="section-eyebrow">Step 2 · Live Pipeline</span>
          <h2>Progress</h2>
        </div>
        <span className={`status-pill status-pill-${job.status}`}>
          <span className="status-dot" aria-hidden="true" />
          <span className="status">{copy}</span>
        </span>
      </div>

      <div className="progress-bar-container">
        <div className="progress-meta-row">
          <p className="muted stage-readout">
            Stage: {job.stage}
            {job.chunk_total > 0
              ? ` · chunks ${job.chunk_current}/${job.chunk_total}`
              : null}
          </p>
          <span className="mono-badge">{percent}%</span>
        </div>

        <div
          className="progress-track"
          role="progressbar"
          aria-label="Job pipeline progress"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={`progress-fill ${
              job.status === "failed"
                ? "progress-fill-error"
                : job.status === "completed"
                  ? "progress-fill-done"
                  : "progress-fill-active"
            }`}
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      <ol className="pipeline-stepper" aria-label="Pipeline stages">
        {steps.map((step, index) => {
          const state = stepVisualState(job.status, job.stage, step.key);
          return (
            <li key={step.key} className={`step-item step-${state}`}>
              <span className="step-indicator" aria-hidden="true">
                {state === "done" ? (
                  <svg viewBox="0 0 20 20" fill="currentColor" className="step-check">
                    <path
                      fillRule="evenodd"
                      d="M16.704 5.29a1 1 0 0 1 .006 1.414l-7.5 7.56a1 1 0 0 1-1.42-.004l-3.5-3.56a1 1 0 1 1 1.42-1.4l2.79 2.838 6.79-6.842a1 1 0 0 1 1.414-.006Z"
                      clipRule="evenodd"
                    />
                  </svg>
                ) : (
                  index + 1
                )}
              </span>
              <span className="step-label">{step.label}</span>
            </li>
          );
        })}
      </ol>

      <div className="job-telemetry">
        <span className="telemetry-tag">
          {job.source_language} → {job.target_language}
        </span>
        {job.voice ? <span className="telemetry-tag">{job.voice}</span> : null}
        <span className="telemetry-tag">{job.speed}x</span>
        <span className="telemetry-tag">{job.output_format.toUpperCase()}</span>
        {job.translation_provider ? (
          <span className="telemetry-tag">
            {job.translation_provider}
            {job.translation_model ? ` (${job.translation_model})` : ""}
          </span>
        ) : null}
      </div>

      {job.status === "failed" && (job.error_type || job.message) ? (() => {
        const { summary, details } = friendlyError(job.error_type, job.message);
        return (
          <div role="alert" className="alert-banner alert-error">
            <p className="alert-summary">{summary}</p>
            {details ? (
              <details className="text-xs mt-1 text-slate-500 cursor-pointer">
                <summary>Technical details</summary>
                <span>{details}</span>
              </details>
            ) : null}
          </div>
        );
      })() : null}
    </section>
  );
}
