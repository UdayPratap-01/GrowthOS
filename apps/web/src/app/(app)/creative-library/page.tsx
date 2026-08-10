"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { MediaPreview } from "@/components/creative/MediaPreview";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import {
  MediaJob,
  mediaPhaseLabel,
  mediaPhaseTone,
  normalizeMediaPhase,
  pollMediaJob,
  type MediaJobStatus,
} from "@/lib/jobs";
import { Client, CreativeAsset } from "@/types";

const TYPE_FILTERS = ["all", "image", "video", "concept", "image_concept", "video_concept", "variation"];

type ProviderStatus = {
  image_provider: string;
  image_configured: boolean;
  video_provider: string;
  video_configured: boolean;
  message: string;
};

type ActiveJob = {
  kind: "images" | "videos";
  jobId: string;
  phase: MediaJobStatus;
  message: string | null;
  error: string | null;
  retryable: boolean;
  lastPayload: MediaJob | null;
};

export default function CreativeLibraryPage() {
  const [assets, setAssets] = useState<CreativeAsset[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [assetType, setAssetType] = useState("all");
  const [prompt, setPrompt] = useState("Premium brand product hero creative, clean lighting");
  const [providers, setProviders] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveJob | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function load() {
    setError(null);
    try {
      const params = new URLSearchParams();
      if (clientId) params.set("client_id", clientId);
      if (assetType !== "all") params.set("asset_type", assetType);
      const suffix = params.toString() ? `?${params}` : "";
      const [a, c, p] = await Promise.all([
        api<CreativeAsset[]>(`/creative/assets${suffix}`),
        api<Client[]>("/clients"),
        api<ProviderStatus>("/creative/providers"),
      ]);
      setAssets(a);
      setClients(c);
      setProviders(p);
      if (!clientId && c[0]) setClientId(c[0].id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, assetType]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const clientName = useMemo(() => {
    const map = Object.fromEntries(clients.map((c) => [c.id, c.business_name]));
    return (id: string) => map[id] || id.slice(0, 8);
  }, [clients]);

  const phase: MediaJobStatus = active?.phase ?? "idle";
  const busy = phase === "queued" || phase === "generating" || phase === "processing" || phase === "uploading";

  async function startGeneration(kind: "images" | "videos") {
    if (!clientId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setError(null);
    setActive({
      kind,
      jobId: "",
      phase: "queued",
      message: "Submitting generation job…",
      error: null,
      retryable: false,
      lastPayload: null,
    });

    try {
      const body =
        kind === "images"
          ? { client_id: clientId, prompt, aspect_ratio: "1:1", quantity: 1 }
          : { client_id: clientId, prompt, aspect_ratio: "9:16", duration_seconds: 5 };

      const accepted = await api<MediaJob>(`/creative/${kind}/generate`, {
        method: "POST",
        body: JSON.stringify(body),
      });

      // Immediate failure (provider not configured) — do not pretend it is queued.
      if (!accepted.job_id || isImmediateFailure(accepted.status)) {
        setActive({
          kind,
          jobId: accepted.job_id || "",
          phase: normalizeMediaPhase(accepted.status),
          message: accepted.message || null,
          error: accepted.error || accepted.message || "Generation failed.",
          retryable: Boolean(accepted.retryable),
          lastPayload: accepted,
        });
        return;
      }

      // Already finished (inline development mode). Only claim success if the
      // server returned COMPLETED with assets — never invent one.
      if (normalizeMediaPhase(accepted.status) === "completed") {
        setActive({
          kind,
          jobId: accepted.job_id,
          phase: "completed",
          message: accepted.message || `Stored${accepted.demo ? " (DEMO)" : ""}.`,
          error: null,
          retryable: false,
          lastPayload: accepted,
        });
        await load();
        return;
      }

      setActive({
        kind,
        jobId: accepted.job_id,
        phase: normalizeMediaPhase(accepted.status),
        message: accepted.message || "Job accepted. Waiting for the worker…",
        error: null,
        retryable: false,
        lastPayload: accepted,
      });

      const finalJob = await pollMediaJob(kind, accepted.job_id, {
        signal: controller.signal,
        onUpdate: (job) => {
          setActive({
            kind,
            jobId: accepted.job_id!,
            phase: normalizeMediaPhase(job.status),
            message: job.message || null,
            error: job.error || null,
            retryable: Boolean(job.retryable),
            lastPayload: job,
          });
        },
      });

      setActive({
        kind,
        jobId: accepted.job_id,
        phase: normalizeMediaPhase(finalJob.status),
        message:
          normalizeMediaPhase(finalJob.status) === "completed"
            ? finalJob.message || `Stored${finalJob.demo ? " (DEMO)" : ""}.`
            : finalJob.message || null,
        error: finalJob.error || null,
        retryable: Boolean(finalJob.retryable),
        lastPayload: finalJob,
      });

      if (normalizeMediaPhase(finalJob.status) === "completed") {
        await load();
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setActive((prev) =>
        prev
          ? {
              ...prev,
              phase: "failed",
              error: errorMessage(err),
              retryable: true,
            }
          : null,
      );
      setError(errorMessage(err));
    }
  }

  async function retryActive() {
    if (!active) return;
    await startGeneration(active.kind);
  }

  async function regenerate(asset: CreativeAsset) {
    setError(null);
    setActive({
      kind: asset.asset_type === "video" ? "videos" : "images",
      jobId: "",
      phase: "queued",
      message: "Submitting variation job…",
      error: null,
      retryable: false,
      lastPayload: null,
    });
    try {
      const result = await api<{ job_id?: string; status?: string; message?: string }>(
        `/creative/${asset.id}/variations`,
        { method: "POST", body: JSON.stringify({ count: 1 }) },
      );
      setActive({
        kind: asset.asset_type === "video" ? "videos" : "images",
        jobId: result.job_id || "",
        phase: normalizeMediaPhase(result.status || "QUEUED"),
        message: result.message || "Variation job submitted. Refresh when it completes.",
        error: null,
        retryable: false,
        lastPayload: null,
      });
      await load();
    } catch (err) {
      setActive((prev) =>
        prev
          ? { ...prev, phase: "failed", error: errorMessage(err), retryable: true }
          : null,
      );
      setError(errorMessage(err));
    }
  }

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error && assets.length === 0) {
    return <EmptyState title="Creative library unavailable" description={error} />;
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Creative Library</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Generation is asynchronous. COMPLETED only means a real file was stored.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/campaign-builder">
            <Button variant="secondary">Campaign Builder</Button>
          </Link>
          <Link href="/autopilot">
            <Button variant="ghost">Autopilot</Button>
          </Link>
        </div>
      </div>

      {providers ? (
        <Card>
          <div className="flex flex-wrap gap-3 text-sm">
            <Badge tone={providers.image_configured ? "success" : "warning"}>
              Image: {providers.image_provider}
              {providers.image_configured ? "" : " — NOT CONFIGURED"}
            </Badge>
            <Badge tone={providers.video_configured ? "success" : "warning"}>
              Video: {providers.video_provider}
              {providers.video_configured ? "" : " — NOT CONFIGURED"}
            </Badge>
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">{providers.message}</p>
        </Card>
      ) : null}

      <Card>
        <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
          <Input
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Generation prompt"
            disabled={busy}
          />
          <Button disabled={busy || !clientId} onClick={() => startGeneration("images")}>
            Generate image
          </Button>
          <Button
            disabled={busy || !clientId}
            variant="secondary"
            onClick={() => startGeneration("videos")}
          >
            Generate video
          </Button>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Badge tone={mediaPhaseTone(phase)}>{mediaPhaseLabel(phase)}</Badge>
          {active?.jobId ? (
            <span className="text-xs text-[var(--muted)]">Job {active.jobId.slice(0, 8)}…</span>
          ) : null}
          {active?.message ? (
            <span className="text-sm text-[var(--accent-ink)]">{active.message}</span>
          ) : null}
          {active?.error ? <span className="text-sm text-red-600">{active.error}</span> : null}
          {phase === "failed" && active?.retryable ? (
            <Button size="sm" variant="secondary" onClick={retryActive}>
              Retry
            </Button>
          ) : null}
        </div>
        {error && assets.length > 0 ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
      </Card>

      <Card>
        <div className="flex flex-wrap gap-3">
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.business_name}
              </option>
            ))}
          </Select>
          <Select className="w-48" value={assetType} onChange={(e) => setAssetType(e.target.value)}>
            {TYPE_FILTERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
          <Button variant="secondary" onClick={load}>
            Refresh
          </Button>
        </div>
      </Card>

      {assets.length === 0 ? (
        <EmptyState
          title="No creatives yet"
          description="Generate an image/video when providers are configured, or build a campaign to create concepts."
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {assets.map((a) => (
            <Card key={a.id}>
              {a.url && (a.asset_type === "image" || a.asset_type === "video") ? (
                <MediaPreview
                  url={a.url}
                  mimeType={a.mime_type}
                  alt={a.name}
                  demo={a.data_source === "demo"}
                />
              ) : (
                <div className="flex h-40 items-center justify-center rounded-xl bg-[var(--surface-2)] text-xs text-[var(--muted)]">
                  {a.asset_type.includes("concept") ? "Concept / prompt only" : "No media file"}
                </div>
              )}
              <div className="mt-3 flex items-start justify-between gap-2">
                <div className="font-medium">{a.name}</div>
                <Badge
                  tone={
                    a.data_source === "demo"
                      ? "demo"
                      : a.status === "completed"
                        ? "success"
                        : a.status === "failed"
                          ? "danger"
                          : "accent"
                  }
                >
                  {a.data_source === "demo" ? "DEMO" : a.status}
                </Badge>
              </div>
              <div className="mt-2 text-xs text-[var(--muted)]">
                {clientName(a.client_id)} · {a.asset_type} · {a.provider || "n/a"}
              </div>
              {a.prompt ? <p className="mt-2 line-clamp-3 text-sm text-[var(--muted)]">{a.prompt}</p> : null}
              <div className="mt-3">
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => regenerate(a)}>
                  Create variation
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function isImmediateFailure(status: string | null | undefined): boolean {
  return normalizeMediaPhase(status) === "failed";
}
