"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { formatCurrency, formatNumber, formatPercent } from "@/lib/utils";
import { Campaign, Client } from "@/types";

export default function CampaignsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [platform, setPlatform] = useState("");
  const [items, setItems] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const clientMap = useMemo(
    () => Object.fromEntries(clients.map((c) => [c.id, c.business_name])),
    [clients]
  );

  useEffect(() => {
    (async () => setClients(await api<Client[]>("/clients")))();
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const qs = new URLSearchParams();
        if (clientId) qs.set("client_id", clientId);
        if (platform) qs.set("platform", platform);
        const suffix = qs.toString() ? `?${qs}` : "";
        setItems(await api<Campaign[]>(`/campaigns${suffix}`));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load campaigns");
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId, platform]);

  if (loading && !items.length && !error) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Campaigns</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Seeded demo campaigns plus live rows synced from Google Ads (Phase 4).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/campaign-builder"><Button size="sm">AI Campaign Builder</Button></Link>
          <Link href="/autopilot"><Button size="sm" variant="secondary">Autopilot</Button></Link>
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.business_name}</option>
            ))}
          </Select>
          <Select className="w-40" value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">All platforms</option>
            <option value="meta">Meta</option>
            <option value="google_ads">Google Ads</option>
            <option value="youtube">YouTube</option>
          </Select>
        </div>
      </div>

      {error ? <EmptyState title="Campaigns unavailable" description={error} /> : null}

      {!error && items.length === 0 ? (
        <EmptyState
          title="No campaigns yet"
          description="Connect Google Ads under Integrations and sync with a client selected, or use demo seed data."
        />
      ) : null}

      {items.length > 0 ? (
        <Card>
          <CardHeader
            title="Campaign performance"
            subtitle="data_source marks demo vs live. Live requires OAuth + sync."
          />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-[var(--muted)]">
                <tr className="border-b border-[var(--line)]">
                  <th className="py-2 pr-3 font-medium">Campaign</th>
                  <th className="py-2 pr-3 font-medium">Client</th>
                  <th className="py-2 pr-3 font-medium">Platform</th>
                  <th className="py-2 pr-3 font-medium">Spend</th>
                  <th className="py-2 pr-3 font-medium">Leads</th>
                  <th className="py-2 pr-3 font-medium">CTR</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => {
                  const leads = Number(c.metrics?.leads ?? c.metrics?.conversions ?? 0);
                  const ctr = c.metrics?.ctr;
                  return (
                    <tr key={c.id} className="border-b border-[var(--line)]/70">
                      <td className="py-3 pr-3 font-medium">
                        <Link className="hover:text-[var(--accent-ink)]" href={`/clients/${c.client_id}?tab=campaigns`}>
                          {c.name}
                        </Link>
                      </td>
                      <td className="py-3 pr-3 text-[var(--muted)]">{clientMap[c.client_id] || "—"}</td>
                      <td className="py-3 pr-3 capitalize">{c.platform.replaceAll("_", " ")}</td>
                      <td className="py-3 pr-3">{formatCurrency(c.spend)}</td>
                      <td className="py-3 pr-3">{formatNumber(leads)}</td>
                      <td className="py-3 pr-3">{formatPercent(ctr ?? null)}</td>
                      <td className="py-3 pr-3 capitalize">{c.status}</td>
                      <td className="py-3">
                        <Badge tone={c.data_source === "live" ? "success" : "accent"}>
                          {c.data_source}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
