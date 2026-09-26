"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Summary = {
  sync_id?: string;
  site_url?: string;
  algorithm_version: string;
  total_topics: number;
  total_clustered_queries: number;
  singleton_topics: number;
  average_queries_per_topic: number;
  multi_page_topic_count: number;
  disclaimer: string;
};

type Topic = {
  id: string;
  topic_label: string;
  representative_query: string;
  query_count: number;
  page_count: number;
  total_impressions: number;
  total_clicks: number;
  aggregate_ctr: number;
  weighted_average_position: number;
  opportunity_count: number;
  multi_page_signal: boolean;
  is_singleton: boolean;
};

type TopicQuery = {
  query: string;
  similarity_score: number;
  impressions: number;
  clicks: number;
  ctr: number;
  average_position: number;
  is_representative: boolean;
};

type TopicPage = {
  page_url: string;
  impressions: number;
  clicks: number;
  ctr: number;
  average_position: number;
  is_primary: boolean;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function TopicClusteringPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [queries, setQueries] = useState<TopicQuery[]>([]);
  const [pages, setPages] = useState<TopicPage[]>([]);
  const [singletonFilter, setSingletonFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await api<Summary>("/seo/topics/summary");
      setSummary(s);
      const params = new URLSearchParams({ limit: "50", sort: "impressions" });
      if (singletonFilter === "singleton") params.set("is_singleton", "true");
      if (singletonFilter === "multi") params.set("multi_page_signal", "true");
      const rows = await api<Topic[]>(`/seo/topics?${params.toString()}`);
      setTopics(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load topics");
    }
  }, [singletonFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAnalysis() {
    setBusy(true);
    setError(null);
    try {
      await api("/seo/topics/analyze", { method: "POST", body: JSON.stringify({}) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  async function openTopic(id: string) {
    setSelectedId(id);
    setError(null);
    try {
      const [q, p] = await Promise.all([
        api<TopicQuery[]>(`/seo/topics/${id}/queries`),
        api<TopicPage[]>(`/seo/topics/${id}/pages`),
      ]);
      setQueries(q);
      setPages(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load topic detail");
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Topic Clustering"
          subtitle="Deterministic query grouping from Search Console — not AI-generated topic names."
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
            <div>Total topics: {summary.total_topics}</div>
            <div>Clustered queries: {summary.total_clustered_queries}</div>
            <div>Singleton topics: {summary.singleton_topics}</div>
            <div>Avg queries/topic: {summary.average_queries_per_topic}</div>
            <div>Multi-page topics: {summary.multi_page_topic_count}</div>
            <div>Algorithm: {safeText(summary.algorithm_version)}</div>
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">{summary.disclaimer}</p>
        </Card>
      ) : null}

      <Card>
        <div className="mb-3">
          <select
            className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
            value={singletonFilter}
            onChange={(e) => setSingletonFilter(e.target.value)}
          >
            <option value="">All topics</option>
            <option value="singleton">Singleton only</option>
            <option value="multi">Multi-page signal</option>
          </select>
        </div>
        {topics.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            No topic clusters yet. Run keyword analysis, then topic clustering after a Search Console sync.
          </p>
        ) : (
          <div className="space-y-2">
            {topics.map((t) => (
              <button
                key={t.id}
                type="button"
                className="w-full rounded-lg border border-[var(--line)] p-3 text-left text-sm hover:bg-[var(--surface)]"
                onClick={() => void openTopic(t.id)}
              >
                <div className="flex flex-wrap gap-2">
                  <span className="font-medium">{safeText(t.topic_label)}</span>
                  {t.multi_page_signal ? (
                    <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">multi-page signal</span>
                  ) : null}
                  {t.is_singleton ? (
                    <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">singleton</span>
                  ) : null}
                </div>
                <div className="text-[var(--muted)]">Rep: {safeText(t.representative_query)}</div>
                <div className="text-[var(--muted)]">
                  {t.query_count} queries · {t.page_count} pages · {t.total_impressions} impressions · {t.total_clicks}{" "}
                  clicks · CTR {(t.aggregate_ctr * 100).toFixed(2)}% · avg position{" "}
                  {t.weighted_average_position.toFixed(1)} · {t.opportunity_count} opportunities
                </div>
              </button>
            ))}
          </div>
        )}
      </Card>

      {selectedId ? (
        <Card>
          <CardHeader title="Topic detail" subtitle={safeText(selectedId)} />
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-medium">Associated queries</h3>
              {queries.length === 0 ? (
                <p className="text-sm text-[var(--muted)]">No queries</p>
              ) : (
                <div className="space-y-2">
                  {queries.map((q) => (
                    <div key={q.query} className="rounded border border-[var(--line)] p-2 text-sm">
                      <div>{safeText(q.query)}</div>
                      <div className="text-[var(--muted)]">
                        similarity {q.similarity_score.toFixed(2)} · {q.impressions} imp · avg pos{" "}
                        {q.average_position.toFixed(1)}
                        {q.is_representative ? " · representative" : ""}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-medium">Associated pages</h3>
              {pages.length === 0 ? (
                <p className="text-sm text-[var(--muted)]">No pages</p>
              ) : (
                <div className="space-y-2">
                  {pages.map((p) => (
                    <div key={p.page_url} className="rounded border border-[var(--line)] p-2 text-sm">
                      <div>{safeText(p.page_url)}</div>
                      <div className="text-[var(--muted)]">
                        {p.impressions} imp · {p.clicks} clicks · CTR {(p.ctr * 100).toFixed(2)}%
                        {p.is_primary ? " · primary" : ""}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
