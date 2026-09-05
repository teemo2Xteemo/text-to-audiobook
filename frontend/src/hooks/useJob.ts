import { useCallback, useEffect, useState } from "react";

import { createJob, getJob, toApiError } from "../api/client";
import { ApiError, type CreateJobInput, type JobStatusResponse } from "../api/types";
import { LAST_JOB_STORAGE_KEY, POLL_INTERVAL_MS } from "../lib/constants";
import { isTerminalStatus } from "../lib/jobUi";

export type JobView =
  | { status: "idle" }
  | { status: "loading"; job?: JobStatusResponse }
  | { status: "success"; job: JobStatusResponse }
  | { status: "error"; error: ApiError; job?: JobStatusResponse };

function failedJobError(job: JobStatusResponse): ApiError {
  return new ApiError(job.error_type ?? "TRANSLATION_FAILED", job.message ?? "job failed", 200);
}

export function jobFromView(view: JobView): JobStatusResponse | undefined {
  if (view.status === "idle") {
    return undefined;
  }
  return view.job;
}

export function useJob(): JobView & {
  submit: (input: CreateJobInput) => Promise<void>;
} {
  const [view, setView] = useState<JobView>({ status: "idle" });

  const applyJob = useCallback((job: JobStatusResponse) => {
    if (job.status === "completed") {
      setView({ status: "success", job });
      return;
    }
    if (job.status === "failed") {
      setView({ status: "error", error: failedJobError(job), job });
      return;
    }
    setView({ status: "loading", job });
  }, []);

  const pollOnce = useCallback(
    async (jobId: string) => {
      const job = await getJob(jobId);
      applyJob(job);
    },
    [applyJob],
  );

  useEffect(() => {
    if (view.status !== "loading" || !view.job || isTerminalStatus(view.job.status)) {
      return;
    }
    const jobId = view.job.job_id;
    const timer = window.setTimeout(() => {
      void pollOnce(jobId).catch((error: unknown) => {
        setView((current) => ({
          status: "error",
          error: toApiError(error),
          job: current.status === "idle" ? undefined : current.job,
        }));
      });
    }, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [view, pollOnce]);

  useEffect(() => {
    const stored = sessionStorage.getItem(LAST_JOB_STORAGE_KEY);
    if (!stored) {
      return;
    }
    setView({ status: "loading" });
    void getJob(stored)
      .then((job) => {
        applyJob(job);
      })
      .catch((error: unknown) => {
        const apiError = toApiError(error);
        if (apiError.status === 404) {
          sessionStorage.removeItem(LAST_JOB_STORAGE_KEY);
          setView({ status: "idle" });
          return;
        }
        setView({ status: "error", error: apiError });
      });
  }, [applyJob]);

  const submit = useCallback(
    async (input: CreateJobInput) => {
      setView({ status: "loading" });
      try {
        const created = await createJob(input);
        sessionStorage.setItem(LAST_JOB_STORAGE_KEY, created.job_id);
        const job = await getJob(created.job_id);
        applyJob(job);
      } catch (error) {
        setView({ status: "error", error: toApiError(error) });
      }
    },
    [applyJob],
  );

  return { ...view, submit };
}
