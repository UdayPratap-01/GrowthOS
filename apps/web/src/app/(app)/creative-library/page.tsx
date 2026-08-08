"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { Client, CreativeAsset } from "@/types";

const TYPE_FILTERS = ["all", "concept", "image_concept", "video_concept", "variation", "image", "video"];

export default function CreativeLibraryPage() {
  const [assets, setAssets] = useState<CreativeAsset[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [assetType, setAssetType] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const params = new URLSearchParams();
      if (clientId) params.set("client_id", clientId);
      if (assetType !== "all") params.set("asset_type", assetType);
      const suffix = params.toString() ? `?${params}` : "";
      const [a, c] = await Promise.all([
        api<CreativeAsset[]>(`/autopilot/creative/library${suffix}`),
        api<Client[]>("/clients"),
      ]);
      setAssets(a);
      setClients(c);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load library");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, assetType]);

  const clientName = useMemo(() => {
    const map = Object.fromEntries(clients.map((c) => [c.id, c.business_name]));
    return (id: string) => map[id] || id.slice(0, 8);
  }, [clients]);

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error && assets.length === 0) return <EmptyState title="Creative library unavailable" description={error} />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Creative Library</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Concepts, prompts, and variations. Real image/video bytes only appear when a provider is configured.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/campaign-builder"><Button variant="secondary">Campaign Builder</Button></Link>
          <Link href="/autopilot"><Button variant="ghost">Autopilot</Button></Link>
        </div>
      </div>

      <Card>
        <div className="flex flex-wrap gap-3">
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.business_name}</option>
            ))}
          </Select>
          <Select className="w-48" value={assetType} onChange={(e) => setAssetType(e.target.value)}>
            {TYPE_FILTERS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </Select>
          <Button variant="secondary" onClick={load}>Refresh</Button>
        </div>
      </Card>

      {assets.length === 0 ? (
        <EmptyState
          title="No creatives yet"
          description="Use Campaign Builder or Autopilot to generate concepts. Image/video generation stays NOT CONFIGURED until a provider is set."
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {assets.map((a) => (
            <Card key={a.id}>
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium">{a.name}</div>
                <Badge tone={a.data_source === "demo" ? "demo" : "accent"}>{a.data_source === "demo" ? "DEMO DATA" : a.status}</Badge>
              </div>
              <div className="mt-2 text-xs text-[var(--muted)]">
                {clientName(a.client_id)} · {a.asset_type} · {a.platform || "n/a"} · {a.provider || "n/a"}
              </div>
              {a.prompt ? (
                <p className="mt-3 line-clamp-4 text-sm text-[var(--muted)]">{a.prompt}</p>
              ) : null}
              {a.content?.headline ? (
                <p className="mt-2 text-sm">{String(a.content.headline)}</p>
              ) : null}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
