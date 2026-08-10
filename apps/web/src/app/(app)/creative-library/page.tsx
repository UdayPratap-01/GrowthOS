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
import { api, downloadMedia, errorMessage } from "@/lib/api";
import {
  MediaJob,
  mediaPhaseLabel,
  mediaPhaseTone,
  normalizeMediaPhase,
  pollMediaJob,
  type MediaJobStatus,
} from "@/lib/jobs";
import { Campaign, Client, CreativeAsset } from "@/types";

const TYPE_FILTERS = ["all", "image", "video", "concept", "image_concept", "video_concept", "variation"];

//: Asset lifecycle states worth filtering by. NOT_CONFIGURED is included because
//: "nothing was generated because no provider exists" is a state a user needs to
//: find, not an error to be buried.
const STATUS_FILTERS = ["all", "completed", "queued", "generating", "failed", "not_configured"];

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
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [clientId, setClientId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [assetType, setAssetType] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [prompt, setPrompt] = useState("Premium brand product hero creative, clean lighting");
  const [providers, setProviders] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveJob | null>(null);
  const [pendingAsset, setPendingAsset] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function load() {
    setError(null);
    try {
      const params = new URLSearchParams();
      if (clientId) params.set("client_id", clientId);
      if (campaignId) params.set("campaign_id", campaignId);
      if (assetType !== "all") params.set("asset_type", assetType);
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (includeArchived) params.set("include_archived", "true");
      const suffix = params.toString() ? `?${params}` : "";
      const campaignQuery = clientId ? `?client_id=${clientId}` : "";
      const [a, c, p, camps] = await Promise.all([
        api<CreativeAsset[]>(`/creative/assets${suffix}`),
        api<Client[]>("/clients"),
        api<ProviderStatus>("/creative/providers"),
        api<Campaign[]>(`/campaigns${campaignQuery}`),
      ]);
      setAssets(a);
      setClients(c);
      setProviders(p);
      setCampaigns(camps);
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
  }, [clientId, campaignId, assetType, statusFilter, includeArchived]);

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

  /** Save a stored asset. Routed through the authenticated API, never a public link. */
  async function download(asset: CreativeAsset) {
    if (!asset.url) return;
    setPendingAsset(asset.id);
    setError(null);
    try {
      await downloadMedia(asset.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPendingAsset(null);
    }
  }

  /** Hide or restore an asset. The stored file is kept either way. */
  async function toggleArchive(asset: CreativeAsset) {
    setPendingAsset(asset.id);
    setError(null);
    try {
      await api<CreativeAsset>(
        `/creative/assets/${asset.id}/archive?archived=${asset.archived_at ? "false" : "true"}`,
        { method: "POST" },
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPendingAsset(null);
    }
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
          <Link href="/ai-campaigns/new">
            <Button>Create campaign with AI</Button>
          </Link>
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
        <div className="flex flex-wrap items-center gap-3">
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.business_name}
              </option>
            ))}
          </Select>
          <Select
            className="w-56"
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
          >
            <option value="">All campaigns</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
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
          <Select
            className="w-48"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s.replaceAll("_", " ")}
              </option>
            ))}
          </Select>
          <label className="flex items-center gap-2 text-sm text-[var(--muted)]">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            Show archived
          </label>
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
          {assets.map((a) => {
            const assetPhase = normalizeMediaPhase(a.status);
            const pending = pendingAsset === a.id;
            return (
              <Card key={a.id} className={a.archived_at ? "opacity-60" : undefined}>
                {a.url && (a.asset_type === "image" || a.asset_type === "video") ? (
                  <MediaPreview
                    url={a.url}
                    mimeType={a.mime_type}
                    alt={a.name}
                    demo={a.data_source === "demo"}
                  />
                ) : (
                  <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-xl bg-[var(--surface-2)] px-3 text-center text-xs text-[var(--muted)]">
                    <Badge tone={mediaPhaseTone(assetPhase)}>{mediaPhaseLabel(assetPhase)}</Badge>
                    {a.asset_type.includes("concept")
                      ? "Concept / prompt only"
                      : assetPhase === "not_configured"
                        ? "No provider configured, so no file was generated."
                        : "No media file"}
                  </div>
                )}
                <div className="mt-3 flex items-start justify-between gap-2">
                  <div className="font-medium">{a.name}</div>
                  <Badge tone={mediaPhaseTone(assetPhase)}>{mediaPhaseLabel(assetPhase)}</Badge>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {/* REAL and DEMO are stated explicitly so demo output can never be
                      mistaken for a production asset. */}
                  <Badge tone={a.data_source === "demo" ? "demo" : "success"}>
                    {a.data_source === "demo" ? "Demo" : "Real"}
                  </Badge>
                  {a.archived_at ? <Badge>Archived</Badge> : null}
                  {a.aspect_ratio ? <Badge tone="low">{a.aspect_ratio}</Badge> : null}
                </div>
                <div className="mt-2 text-xs text-[var(--muted)]">
                  {clientName(a.client_id)} · {a.asset_type} · {a.provider || "n/a"}
                </div>
                {a.prompt ? (
                  <p className="mt-2 line-clamp-3 text-sm text-[var(--muted)]">{a.prompt}</p>
                ) : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => regenerate(a)}>
                    Create variation
                  </Button>
                  {a.url ? (
                    <Button size="sm" variant="ghost" disabled={pending} onClick={() => download(a)}>
                      {pending ? "Preparing…" : "Download"}
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pending}
                    onClick={() => toggleArchive(a)}
                  >
                    {a.archived_at ? "Restore" : "Archive"}
                  </Button>
                  {a.campaign_id ? (
                    <Link href={`/ai-campaigns/${a.campaign_id}`}>
                      <Button size="sm" variant="ghost">
                        Campaign
                      </Button>
                    </Link>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function isImmediateFailure(status: string | null | undefined): boolean {
  return normalizeMediaPhase(status) === "failed";
}
