import type { JobStatusResponse } from "../api/types";
import { progressCopy } from "../lib/jobUi";

type JobProgressProps = {
  job: JobStatusResponse;
};

export function JobProgress({ job }: JobProgressProps) {
  const copy = progressCopy(job.status, job.chunk_current, job.chunk_total);
  return (
    <section className="panel" aria-live="polite">
      <h2>Progress</h2>
      <p>
        <span className="status">{copy}</span>
      </p>
      <p className="muted">
        Stage: {job.stage}
        {job.chunk_total > 0
          ? ` · chunks ${job.chunk_current}/${job.chunk_total}`
          : null}
      </p>
      {job.status === "failed" && (job.error_type || job.message) ? (
        <p role="alert">
          {job.error_type}
          {job.message ? `: ${job.message}` : ""}
        </p>
      ) : null}
    </section>
  );
}
