"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Summary = {
  total_gaps: number;
  by_gap_type: Record<string, number>;
  by_match_strength: Record<string, number>;
  active_competitors: number;
  competitor_pages_observed: number;
  has_competitor_data: boolean;
  algorithm_version: string;
  disclaimer: string;
};

type Gap = {
  id: string;
  gap_type: string;
  topic_label: string;
  competitor_url?: string | null;
  user_query?: string | null;
  similarity: number;
  match_strength: string;
  explanation: string;
  evidence: Record<string, unknown>;
  created_at?: string;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function ContentGapsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [gapTypeFilter, setGapTypeFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await api<Summary>("/seo/content-gaps/summary");
      setSummary(s);
      const params = new URLSearchParams({ limit: "50", sort: "similarity" });
      if (gapTypeFilter) params.set("gap_type", gapTypeFilter);
      const rows = await api<Gap[]>(`/seo/content-gaps?${params.toString()}`);
      setGaps(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load content gaps");
    }
  }, [gapTypeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAnalysis() {
    setBusy(true);
    setError(null);
    try {
      await api("/seo/content-gaps/analyze", { method: "POST", body: JSON.stringify({}) });
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
          title="Content Gap Analysis"
          subtitle="Deterministic content-gap signals from user topics and competitor crawl observations."
        />
        <Button disabled={busy} onClick={runAnalysis}>
          {busy ? "Analyzing…" : "Run analysis"}
        </Button>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      {summary ? (
        <Card>
          <CardHeader title="Summary" />
          {!summary.has_competitor_data ? (
            <p className="text-sm text-amber-700">Insufficient competitor data — add competitors and run a crawl first.</p>
          ) : null}
          <div className="grid gap-2 text-sm md:grid-cols-3">
            <div>Total gaps: {summary.total_gaps}</div>
            <div>Competitors: {summary.active_competitors}</div>
            <div>Pages observed: {summary.competitor_pages_observed}</div>
            <div>Algorithm: {safeText(summary.algorithm_version)}</div>
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">{summary.disclaimer}</p>
        </Card>
      ) : null}

      <Card>
        <div className="mb-3">
          <select
            className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
            value={gapTypeFilter}
            onChange={(e) => setGapTypeFilter(e.target.value)}
          >
            <option value="">All gap types</option>
            {[
              "COMPETITOR_TOPIC_NO_USER_CLUSTER",
              "COMPETITOR_PAGE_WEAK_USER_COVERAGE",
              "USER_OPPORTUNITY_NO_STRONG_PAGE",
              "MULTI_COMPETITOR_LIMITED_USER",
              "USER_TOPIC_COMPETITOR_DEPTH",
              "COMPETITOR_LEXICAL_NO_USER_TOPIC",
            ].map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        {gaps.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No content-gap signals yet. Configure competitors, crawl, and run topic analysis first.</p>
        ) : (
          <div className="space-y-2">
            {gaps.map((g) => (
              <div key={g.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <span className="font-medium">{safeText(g.topic_label)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(g.gap_type)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(g.match_strength)}</span>
                </div>
                {g.competitor_url ? <div className="text-[var(--muted)]">{safeText(g.competitor_url)}</div> : null}
                {g.user_query ? <div className="text-[var(--muted)]">User query: {safeText(g.user_query)}</div> : null}
                <div className="text-[var(--muted)]">Similarity: {g.similarity.toFixed(2)}</div>
                <p className="mt-1">{safeText(g.explanation)}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
