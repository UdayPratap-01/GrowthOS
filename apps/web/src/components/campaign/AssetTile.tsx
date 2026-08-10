"use client";

import { useState } from "react";
import { MediaPreview } from "@/components/creative/MediaPreview";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { downloadMedia, errorMessage } from "@/lib/api";
import { mediaPhaseLabel, mediaPhaseTone, normalizeMediaPhase } from "@/lib/jobs";
import type { ConceptAsset } from "@/types/campaign-generation";

/**
 * One generated file — or an honest account of why there isn't one.
 *
 * A tile only renders media when the server supplied a url, which it only does
 * for bytes that are actually in storage. Everything else renders its status:
 * QUEUED and GENERATING mean come back later, NOT_CONFIGURED means an operator
 * must add credentials, FAILED means the provider was asked and refused.
 */
export function AssetTile({ asset, className }: { asset: ConceptAsset; className?: string }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const phase = normalizeMediaPhase(asset.status);

  async function save() {
    if (!asset.url) return;
    setDownloading(true);
    setError(null);
    try {
      await downloadMedia(asset.url);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className={`rounded-xl border border-[var(--line)] p-3 ${className || ""}`}>
      {asset.url ? (
        <MediaPreview
          url={asset.url}
          mimeType={asset.mime_type}
          alt={`${asset.kind} creative`}
          demo={asset.demo}
        />
      ) : (
        <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-xl bg-[var(--surface-2)] px-3 text-center">
          <Badge tone={mediaPhaseTone(phase)}>{mediaPhaseLabel(phase)}</Badge>
          <span className="text-xs text-[var(--muted)]">{placeholderText(asset)}</span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
        <Badge tone={mediaPhaseTone(phase)}>{mediaPhaseLabel(phase)}</Badge>
        {asset.demo ? <Badge tone="demo">Demo</Badge> : null}
        <span className="uppercase tracking-wide">{asset.kind}</span>
        {asset.aspect_ratio ? <span>{asset.aspect_ratio}</span> : null}
        {asset.provider ? <span>{asset.provider}</span> : null}
        {asset.duration_seconds ? <span>{asset.duration_seconds}s</span> : null}
      </div>

      {asset.error ? (
        <p className="mt-2 text-xs text-red-600">
          {asset.error}
          {asset.retryable ? " Regenerate to try again." : ""}
        </p>
      ) : null}
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}

      {asset.url ? (
        <Button
          size="sm"
          variant="ghost"
          className="mt-2"
          disabled={downloading}
          onClick={save}
        >
          {downloading ? "Preparing…" : "Download"}
        </Button>
      ) : null}
    </div>
  );
}

function placeholderText(asset: ConceptAsset): string {
  const phase = normalizeMediaPhase(asset.status);
  if (phase === "not_configured") {
    return `No ${asset.kind} provider is configured, so nothing was generated.`;
  }
  if (phase === "failed") return asset.error || "The provider did not return a file.";
  if (phase === "cancelled") return "Generation was cancelled.";
  return "No file yet. This tile fills in when the worker stores the result.";
}
