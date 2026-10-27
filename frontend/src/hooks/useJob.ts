import { useCallback, useEffect, useRef, useState } from "react";

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

function isTransientPollError(error: unknown): boolean {
  const apiError = toApiError(error);
  return (
    apiError.status === 0 ||
    apiError.status === 408 ||
    apiError.status === 429 ||
    apiError.status >= 500
  );
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
  const [retryTick, setRetryTick] = useState(0);
  const failureCountRef = useRef(0);

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
      failureCountRef.current = 0;
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
        const apiError = toApiError(error);
        if (!isTransientPollError(error)) {
          setView((current) => ({
            status: "error",
            error: apiError,
            job: current.status === "idle" ? undefined : current.job,
          }));
          return;
        }
        failureCountRef.current += 1;
        if (failureCountRef.current >= 3) {
          setView((current) => ({
            status: "error",
            error: apiError,
            job: current.status === "idle" ? undefined : current.job,
          }));
          return;
        }
        setRetryTick((tick) => tick + 1);
      });
    }, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [view, pollOnce, retryTick]);

  useEffect(() => {
    const stored = sessionStorage.getItem(LAST_JOB_STORAGE_KEY);
    if (!stored) {
      return;
    }
    failureCountRef.current = 0;
    setView({ status: "loading" });
    void getJob(stored)
      .then((job) => {
        failureCountRef.current = 0;
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
      failureCountRef.current = 0;
      setView({ status: "loading" });
      try {
        const created = await createJob(input);
        sessionStorage.setItem(LAST_JOB_STORAGE_KEY, created.job_id);
        const job = await getJob(created.job_id);
        failureCountRef.current = 0;
        applyJob(job);
      } catch (error) {
        setView({ status: "error", error: toApiError(error) });
      }
    },
    [applyJob],
  );

  return { ...view, submit };
}
