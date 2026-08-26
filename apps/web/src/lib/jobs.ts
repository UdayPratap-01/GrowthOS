/**
 * Async job helpers.
 *
 * Long-running work (image/video generation, report export) returns a job id
 * immediately. The frontend must poll — never wait inside the original request
 * for a video to finish.
 */

import { api } from "@/lib/api";

export type MediaJobStatus =
  | "idle"
  | "queued"
  | "generating"
  | "processing"
  | "uploading"
  | "completed"
  | "failed"
  | "cancelled"
  // A provider was never configured. Distinct from `failed` because nothing was
  // attempted and no retry will help until an operator adds credentials.
  | "not_configured";

export type MediaJob = {
  job_id: string | null;
  status: string;
  assets?: { id: string; url: string; mime_type: string; demo?: boolean }[];
  error?: string | null;
  error_code?: string | null;
  retryable?: boolean;
  message?: string | null;
  demo?: boolean;
};

export type BackgroundJob = {
  id: string;
  job_type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  error: string | null;
  result: Record<string, unknown>;
  terminal: boolean;
};

/** Map a raw provider/job status into a UI phase. Success is never inferred. */
export function normalizeMediaPhase(status: string | null | undefined): MediaJobStatus {
  const value = (status || "").toUpperCase();
  if (!value) return "idle";
  if (value === "QUEUED" || value === "SUBMITTED") return "queued";
  if (value === "GENERATING" || value === "RUNNING" || value === "RETRYING") return "generating";
  if (value === "PROCESSING" || value === "DOWNLOADING") return "processing";
  if (value === "UPLOADING") return "uploading";
  if (value === "COMPLETED") return "completed";
  if (value === "FAILED") return "failed";
  if (value === "CANCELLED") return "cancelled";
  if (value === "NOT_CONFIGURED") return "not_configured";
  return "generating";
}

export function mediaPhaseLabel(phase: MediaJobStatus): string {
  switch (phase) {
    case "idle":
      return "Idle";
    case "queued":
      return "Queued";
    case "generating":
      return "Generating";
    case "processing":
      return "Processing";
    case "uploading":
      return "Uploading";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    case "not_configured":
      return "Not configured";
  }
}

export function mediaPhaseTone(phase: MediaJobStatus): string {
  switch (phase) {
    case "completed":
      return "success";
    case "failed":
    case "cancelled":
      return "danger";
    case "queued":
    case "not_configured":
      return "warning";
    case "generating":
    case "processing":
    case "uploading":
      return "accent";
    default:
      return "default";
  }
}

export function isTerminalMediaStatus(status: string | null | undefined): boolean {
  const phase = normalizeMediaPhase(status);
  return (
    phase === "completed" ||
    phase === "failed" ||
    phase === "cancelled" ||
    phase === "not_configured"
  );
}

/**
 * Poll a media job until it reaches a terminal state.
 *
 * Returns the last payload. Never invents a COMPLETED result — if the server
 * says FAILED, that is what the caller gets.
 */
export async function pollMediaJob(
  kind: "images" | "videos",
  jobId: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
    onUpdate?: (job: MediaJob) => void;
  } = {},
): Promise<MediaJob> {
  const intervalMs = options.intervalMs ?? 1500;
  const timeoutMs = options.timeoutMs ?? 10 * 60 * 1000;
  const started = Date.now();
  let last: MediaJob = { job_id: jobId, status: "QUEUED" };

  while (Date.now() - started < timeoutMs) {
    if (options.signal?.aborted) {
      throw new DOMException("Polling aborted", "AbortError");
    }
    last = await api<MediaJob>(`/creative/${kind}/jobs/${jobId}`);
    options.onUpdate?.(last);
    if (isTerminalMediaStatus(last.status)) {
      return last;
    }
    await sleep(intervalMs, options.signal);
  }

  return {
    ...last,
    status: "FAILED",
    error: last.error || "Timed out waiting for the generation job to finish.",
    retryable: true,
  };
}

export async function pollBackgroundJob(
  jobId: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
    onUpdate?: (job: BackgroundJob) => void;
  } = {},
): Promise<BackgroundJob> {
  const intervalMs = options.intervalMs ?? 1500;
  const timeoutMs = options.timeoutMs ?? 10 * 60 * 1000;
  const started = Date.now();
  let last = await api<BackgroundJob>(`/jobs/${jobId}`);
  options.onUpdate?.(last);

  while (!last.terminal && Date.now() - started < timeoutMs) {
    if (options.signal?.aborted) {
      throw new DOMException("Polling aborted", "AbortError");
    }
    await sleep(intervalMs, options.signal);
    last = await api<BackgroundJob>(`/jobs/${jobId}`);
    options.onUpdate?.(last);
  }
  return last;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Polling aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Polling aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/** Campaign / AI action lifecycle labels the UI is allowed to show. */
export const CAMPAIGN_LIFECYCLE = [
  "draft",
  "pending_approval",
  "approved",
  "executing",
  "published",
  "failed",
] as const;

export type CampaignLifecycle = (typeof CAMPAIGN_LIFECYCLE)[number];

export function normalizeActionLifecycle(status: string | null | undefined): CampaignLifecycle {
  const value = (status || "").toUpperCase();
  if (value === "PENDING" || value === "WAITING_APPROVAL") return "pending_approval";
  if (value === "APPROVED" || value === "SCHEDULED") return "approved";
  if (value === "EXECUTING" || value === "RUNNING") return "executing";
  if (value === "COMPLETED" || value === "PUBLISHED") return "published";
  if (value === "FAILED" || value === "REJECTED" || value === "CANCELLED" || value === "EXPIRED") {
    return "failed";
  }
  return "draft";
}

export const INTEGRATION_STATUSES = [
  "not_connected",
  "connecting",
  "connected",
  "sync_error",
  "disconnected",
  "demo_data",
] as const;

export type IntegrationLifecycle = (typeof INTEGRATION_STATUSES)[number];
