"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Summary = {
  sync_id?: string;
  site_url?: string;
  unique_queries: number;
  total_opportunities: number;
  by_type: Record<string, number>;
  by_priority: Record<string, number>;
  near_page_one_count: number;
  high_impression_low_ctr_count: number;
  disclaimer: string;
};

type Opportunity = {
  id: string;
  query: string;
  page_url?: string | null;
  opportunity_type: string;
  rule_id: string;
  priority: string;
  impressions: number;
  clicks: number;
  ctr: number;
  average_position: number;
  explanation: string;
  change_metrics?: Record<string, unknown>;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function KeywordOpportunitiesPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [priorityFilter, setPriorityFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await api<Summary>("/seo/keywords/summary");
      setSummary(s);
      const params = new URLSearchParams({ limit: "50", sort: "impressions" });
      if (priorityFilter) params.set("priority", priorityFilter);
      const rows = await api<Opportunity[]>(`/seo/keywords?${params.toString()}`);
      setOpportunities(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load keyword opportunities");
    }
  }, [priorityFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAnalysis() {
    setBusy(true);
    setError(null);
    try {
      await api("/seo/keywords/analyze", { method: "POST", body: JSON.stringify({}) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Keyword Opportunities"
          subtitle="Deterministic signals from Search Console — not search volume or traffic forecasts."
        />
        <Button disabled={busy} onClick={runAnalysis}>
          {busy ? "Analyzing…" : "Run analysis"}
        </Button>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      {summary ? (
        <Card>
          <CardHeader title="Summary" subtitle={safeText(summary.site_url)} />
          <div className="grid gap-2 text-sm md:grid-cols-3">
            <div>Unique queries: {summary.unique_queries}</div>
            <div>Opportunities: {summary.total_opportunities}</div>
            <div>Near page one: {summary.near_page_one_count}</div>
            <div>High impression / low CTR: {summary.high_impression_low_ctr_count}</div>
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">{summary.disclaimer}</p>
        </Card>
      ) : null}

      <Card>
        <div className="mb-3">
          <select
            className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
          >
            <option value="">All priorities</option>
            {["high", "medium", "low", "info"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        {opportunities.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No keyword opportunities yet. Run analysis after a Search Console sync.</p>
        ) : (
          <div className="space-y-2">
            {opportunities.map((o) => (
              <div key={o.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <span className="font-medium">{safeText(o.query)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(o.priority)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(o.opportunity_type)}</span>
                </div>
                {o.page_url ? <div className="text-[var(--muted)]">{safeText(o.page_url)}</div> : null}
                <div className="text-[var(--muted)]">
                  {o.impressions} impressions · {o.clicks} clicks · CTR {(o.ctr * 100).toFixed(2)}% · avg position{" "}
                  {o.average_position.toFixed(1)}
                </div>
                <p className="mt-1">{safeText(o.explanation)}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
