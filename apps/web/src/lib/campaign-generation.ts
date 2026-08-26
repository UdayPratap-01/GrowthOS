/**
 * Client for the P2-A campaign engine.
 *
 * Generation is asynchronous end to end: `startGeneration` returns as soon as
 * the run is accepted and the caller polls. Every progress number the UI shows
 * comes from `run.stages`, which the worker writes from real counts — nothing
 * here estimates, interpolates, or animates a fake percentage.
 */

import { api } from "@/lib/api";
import type {
  CampaignGenerateRequest,
  CampaignGenerationRun,
  CampaignGeneratorOptions,
  CampaignPackage,
  CreativeConcept,
  GeneratedCampaign,
  GenerationStage,
  ReviewStatus,
  VariationAxis,
} from "@/types/campaign-generation";

const BASE = "/campaign-generation";

export function fetchGeneratorOptions() {
  return api<CampaignGeneratorOptions>(`${BASE}/options`);
}

export function startGeneration(body: CampaignGenerateRequest) {
  return api<CampaignGenerationRun>(`${BASE}/generate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchRun(runId: string) {
  return api<CampaignGenerationRun>(`${BASE}/runs/${runId}`);
}

export function fetchRuns(params: { clientId?: string; limit?: number } = {}) {
  const query = new URLSearchParams();
  if (params.clientId) query.set("client_id", params.clientId);
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query}` : "";
  return api<CampaignGenerationRun[]>(`${BASE}/runs${suffix}`);
}

export function fetchGeneratedCampaigns(params: { clientId?: string; reviewStatus?: string } = {}) {
  const query = new URLSearchParams();
  if (params.clientId) query.set("client_id", params.clientId);
  if (params.reviewStatus) query.set("review_status", params.reviewStatus);
  const suffix = query.toString() ? `?${query}` : "";
  return api<GeneratedCampaign[]>(`${BASE}/campaigns${suffix}`);
}

export function fetchPackage(campaignId: string) {
  return api<CampaignPackage>(`${BASE}/campaigns/${campaignId}/package`);
}

export function approveCampaign(campaignId: string, comment?: string) {
  return api<CampaignPackage>(`${BASE}/campaigns/${campaignId}/approve`, {
    method: "POST",
    body: JSON.stringify({ comment: comment || null }),
  });
}

export function rejectCampaign(campaignId: string, reason: string) {
  return api<CampaignPackage>(`${BASE}/campaigns/${campaignId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function createVariations(
  conceptId: string,
  body: { count: number; axes?: VariationAxis[]; generate_media?: boolean },
) {
  return api<CreativeConcept>(`${BASE}/concepts/${conceptId}/variations`, {
    method: "POST",
    body: JSON.stringify({
      count: body.count,
      axes: body.axes || [],
      generate_media: Boolean(body.generate_media),
    }),
  });
}

export function regenerateConcept(
  conceptId: string,
  body: { image_quantity: number; video_quantity: number; aspect_ratio?: string | null },
) {
  return api<CreativeConcept>(`${BASE}/concepts/${conceptId}/regenerate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function archiveConcept(conceptId: string, archived: boolean) {
  return api<CreativeConcept>(
    `${BASE}/concepts/${conceptId}/archive?archived=${archived ? "true" : "false"}`,
    { method: "POST" },
  );
}

/**
 * Poll a run until it stops changing.
 *
 * `terminal` comes from the server rather than being inferred from a status
 * string, so a state added later does not silently make this loop exit early.
 */
export async function pollRun(
  runId: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
    onUpdate?: (run: CampaignGenerationRun) => void;
  } = {},
): Promise<CampaignGenerationRun> {
  const intervalMs = options.intervalMs ?? 2000;
  const timeoutMs = options.timeoutMs ?? 15 * 60 * 1000;
  const started = Date.now();

  let run = await fetchRun(runId);
  options.onUpdate?.(run);

  while (!run.terminal && Date.now() - started < timeoutMs) {
    await sleep(intervalMs, options.signal);
    run = await fetchRun(runId);
    options.onUpdate?.(run);
  }
  return run;
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

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

/** Tone for a stage row. NOT_CONFIGURED is a distinct state, not a failure. */
export function stageTone(status: string): string {
  switch (status.toUpperCase()) {
    case "COMPLETED":
      return "success";
    case "RUNNING":
      return "accent";
    case "FAILED":
      return "danger";
    case "NOT_CONFIGURED":
      return "warning";
    case "SKIPPED":
      return "low";
    default:
      return "default";
  }
}

/** The glyph shown beside a stage. Only COMPLETED earns a tick. */
export function stageGlyph(status: string): string {
  switch (status.toUpperCase()) {
    case "COMPLETED":
      return "✓";
    case "RUNNING":
      return "⏳";
    case "FAILED":
      return "!";
    case "NOT_CONFIGURED":
      return "—";
    case "SKIPPED":
      return "·";
    default:
      return "○";
  }
}

/**
 * "Images 2/3" for a counted stage, or the plain status for an uncounted one.
 *
 * Returns null when there is nothing truthful to add, so the caller renders the
 * label alone instead of a misleading "0/0".
 */
export function stageProgressLabel(stage: GenerationStage): string | null {
  if (stage.total > 0) return `${stage.completed}/${stage.total}`;
  return null;
}

export function reviewStatusTone(status: string | null | undefined): string {
  switch ((status || "").toUpperCase()) {
    case "READY_TO_PUBLISH":
    case "APPROVED":
      return "success";
    case "READY_FOR_REVIEW":
      return "accent";
    case "REJECTED":
      return "danger";
    case "GENERATING":
      return "warning";
    default:
      return "default";
  }
}

export function reviewStatusLabel(status: string | null | undefined): string {
  const value = (status || "DRAFT").toUpperCase();
  return value.replaceAll("_", " ");
}

export function isReviewStatus(value: string): value is ReviewStatus {
  return [
    "DRAFT",
    "GENERATING",
    "READY_FOR_REVIEW",
    "APPROVED",
    "REJECTED",
    "READY_TO_PUBLISH",
  ].includes(value.toUpperCase());
}

/** Human label for a variation axis, e.g. `audience_angle` → "Audience angle". */
export function axisLabel(axis: string): string {
  const value = axis.replaceAll("_", " ");
  return value.charAt(0).toUpperCase() + value.slice(1);
}
