"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Summary = {
  site_url?: string | null;
  connected: boolean;
  totals: { clicks?: number; impressions?: number; ctr?: number; compare?: Record<string, number> };
  by_priority: Record<string, number>;
  opportunity_count: number;
  disclaimer: string;
  last_sync?: { id: string; status: string; row_count: number; opportunity_count: number } | null;
};

type PerformanceRow = {
  query?: string | null;
  page_url?: string | null;
  clicks: number;
  impressions: number;
  ctr: number;
  average_position: number;
  metrics_delta?: Record<string, unknown>;
};

type Opportunity = {
  id: string;
  rule_id: string;
  priority: string;
  opportunity_type: string;
  query?: string | null;
  page_url?: string | null;
  clicks: number;
  impressions: number;
  ctr: number;
  average_position: number;
  explanation: string;
};

function safeText(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

export default function SearchConsoleIntelligencePage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [queries, setQueries] = useState<PerformanceRow[]>([]);
  const [pages, setPages] = useState<PerformanceRow[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [preset, setPreset] = useState("last_28_days");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await api<Summary>("/seo/search-console/summary");
      setSummary(s);
      if (s.connected && s.last_sync?.status === "completed") {
        const [q, p, o] = await Promise.all([
          api<PerformanceRow[]>("/seo/search-console/queries?limit=20"),
          api<PerformanceRow[]>("/seo/search-console/pages?limit=20"),
          api<Opportunity[]>("/seo/search-console/opportunities?limit=20"),
        ]);
        setQueries(q);
        setPages(p);
        setOpportunities(o);
      } else {
        setQueries([]);
        setPages([]);
        setOpportunities([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Search Console data");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runSync() {
    setBusy(true);
    setError(null);
    try {
      await api("/seo/search-console/sync", {
        method: "POST",
        body: JSON.stringify({ preset, include_comparison: true }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Search Console Intelligence"
          subtitle="Read-only Google Search Console data — no website mutations."
        />
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-[var(--muted)]">Date range</label>
            <select
              className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              <option value="last_7_days">Last 7 days</option>
              <option value="last_28_days">Last 28 days</option>
              <option value="last_90_days">Last 90 days</option>
            </select>
          </div>
          <Button disabled={busy || !summary?.connected} onClick={runSync}>
            {busy ? "Syncing…" : "Sync now"}
          </Button>
        </div>
        {!summary?.connected ? (
          <p className="mt-3 text-sm text-[var(--muted)]">
            Search Console is not connected. Connect via Integrations to sync live data.
          </p>
        ) : null}
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      {summary ? (
        <Card>
          <CardHeader title="Overview" subtitle={safeText(summary.site_url) || "No property"} />
          <div className="grid gap-3 text-sm md:grid-cols-4">
            <div>Clicks: {summary.totals?.clicks ?? 0}</div>
            <div>Impressions: {summary.totals?.impressions ?? 0}</div>
            <div>CTR: {((summary.totals?.ctr ?? 0) * 100).toFixed(2)}%</div>
            <div>Opportunities: {summary.opportunity_count}</div>
          </div>
          {summary.last_sync ? (
            <p className="mt-2 text-xs text-[var(--muted)]">
              Last sync: {summary.last_sync.status} · {summary.last_sync.row_count} rows ·{" "}
              {summary.last_sync.opportunity_count} opportunities
            </p>
          ) : null}
          <p className="mt-2 text-xs text-[var(--muted)]">{summary.disclaimer}</p>
        </Card>
      ) : null}

      {opportunities.length ? (
        <Card>
          <CardHeader title="Opportunities" subtitle={`${opportunities.length} detected`} />
          <div className="space-y-2">
            {opportunities.map((o) => (
              <div key={o.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <span className="font-medium">{safeText(o.rule_id)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(o.priority)}</span>
                </div>
                {o.query ? <div className="text-[var(--muted)]">Query: {safeText(o.query)}</div> : null}
                {o.page_url ? <div className="text-[var(--muted)]">Page: {safeText(o.page_url)}</div> : null}
                <p className="mt-1">{safeText(o.explanation)}</p>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {queries.length ? (
        <Card>
          <CardHeader title="Top queries" subtitle={`${queries.length} rows`} />
          <div className="space-y-2 text-sm">
            {queries.map((q, i) => (
              <div key={`${q.query}-${i}`} className="rounded-lg border border-[var(--line)] p-3">
                <div className="font-medium">{safeText(q.query)}</div>
                <div className="text-[var(--muted)]">
                  {q.clicks} clicks · {q.impressions} impressions · CTR {(q.ctr * 100).toFixed(2)}% · pos{" "}
                  {q.average_position.toFixed(1)}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {pages.length ? (
        <Card>
          <CardHeader title="Top pages" subtitle={`${pages.length} rows`} />
          <div className="space-y-2 text-sm">
            {pages.map((p, i) => (
              <div key={`${p.page_url}-${i}`} className="rounded-lg border border-[var(--line)] p-3">
                <div className="font-medium">{safeText(p.page_url)}</div>
                <div className="text-[var(--muted)]">
                  {p.clicks} clicks · {p.impressions} impressions · CTR {(p.ctr * 100).toFixed(2)}% · pos{" "}
                  {p.average_position.toFixed(1)}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
