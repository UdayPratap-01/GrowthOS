"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Summary = {
  sync_id?: string;
  total_recommendations: number;
  by_type: Record<string, number>;
  by_priority: Record<string, number>;
  algorithm_version: string;
  prompt_version: string;
  disclaimer: string;
};

type EvidenceRef = {
  source: string;
  id: string;
  reason: string;
};

type Recommendation = {
  id: string;
  run_id: string;
  sync_id?: string | null;
  recommendation_type: string;
  title: string;
  summary: string;
  rationale: string;
  priority: string;
  impact: string;
  effort: string;
  confidence: number;
  status: string;
  evidence_refs: EvidenceRef[];
  affected_urls: string[];
  affected_keywords: string[];
  affected_topics: string[];
  competitor_context: Record<string, unknown>;
  recommended_action: string;
  expected_outcome: string;
  limitations: string[];
  provider: string;
  model: string;
  prompt_version: string;
  algorithm_version: string;
  created_at?: string;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function SeoRecommendationsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const s = await api<Summary>("/seo/recommendations/summary");
      setSummary(s);
      const params = new URLSearchParams({ limit: "50", sort: "confidence" });
      if (typeFilter) params.set("type", typeFilter);
      if (priorityFilter) params.set("priority", priorityFilter);
      const rows = await api<Recommendation[]>(`/seo/recommendations?${params.toString()}`);
      setRecommendations(rows);
      if (rows.length && !selectedId) setSelectedId(rows[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load recommendations");
    }
  }, [typeFilter, priorityFilter, selectedId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await api("/seo/recommendations/generate", { method: "POST", body: JSON.stringify({}) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  const selected = recommendations.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="AI SEO Recommendations"
          subtitle="AI interpretations of deterministic GrowthOS SEO evidence — not guaranteed outcomes."
        />
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={generate}>
            {busy ? "Generating…" : "Generate recommendations"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => void load()}>
            Refresh
          </Button>
        </div>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      {summary ? (
        <Card>
          <CardHeader title="Summary" />
          <div className="grid gap-2 text-sm md:grid-cols-3">
            <div>Total: {summary.total_recommendations}</div>
            <div>Algorithm: {safeText(summary.algorithm_version)}</div>
            <div>Prompt: {safeText(summary.prompt_version)}</div>
          </div>
          <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            {summary.disclaimer}
          </p>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex flex-wrap gap-2">
            <select
              className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All types</option>
              {[
                "technical_seo",
                "content_refresh",
                "keyword_targeting",
                "topic_expansion",
                "content_gap",
                "metadata_optimization",
                "page_structure",
                "search_intent",
                "competitor_gap",
              ].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={priorityFilter}
              onChange={(e) => setPriorityFilter(e.target.value)}
            >
              <option value="">All priorities</option>
              {["high", "medium", "low"].map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          {recommendations.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">
              No recommendations yet. Run SEO analysis pipelines (crawl, keywords, topics, gaps) then generate.
            </p>
          ) : (
            <div className="space-y-2">
              {recommendations.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => setSelectedId(r.id)}
                  className={`w-full rounded-lg border p-3 text-left text-sm transition ${
                    selectedId === r.id
                      ? "border-[var(--accent)] bg-[var(--surface)]"
                      : "border-[var(--line)] hover:bg-[var(--surface)]"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{safeText(r.title)}</span>
                    <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(r.recommendation_type)}</span>
                    <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(r.priority)}</span>
                  </div>
                  <div className="mt-1 text-xs text-[var(--muted)]">
                    Confidence: {(r.confidence * 100).toFixed(0)}% · Impact: {safeText(r.impact)} · Effort: {safeText(r.effort)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Recommendation detail" subtitle="AI recommendation — verify against cited deterministic evidence." />
          {!selected ? (
            <p className="text-sm text-[var(--muted)]">Select a recommendation to view details.</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-[var(--muted)]">AI recommendation</div>
                <h3 className="text-lg font-medium">{safeText(selected.title)}</h3>
                <p className="mt-1">{safeText(selected.summary)}</p>
              </div>
              <div>
                <div className="font-medium">Rationale</div>
                <p className="text-[var(--muted)]">{safeText(selected.rationale)}</p>
              </div>
              <div>
                <div className="font-medium">Recommended action</div>
                <p>{safeText(selected.recommended_action)}</p>
              </div>
              <div>
                <div className="font-medium">Expected outcome</div>
                <p className="text-[var(--muted)]">{safeText(selected.expected_outcome)}</p>
              </div>
              <div>
                <div className="font-medium">Deterministic evidence refs</div>
                <ul className="mt-1 space-y-1">
                  {selected.evidence_refs.map((ref, i) => (
                    <li key={`${ref.source}-${ref.id}-${i}`} className="rounded border border-[var(--line)] p-2 text-xs">
                      <span className="font-mono">{safeText(ref.source)}</span> · {safeText(ref.id)}
                      <div className="text-[var(--muted)]">{safeText(ref.reason)}</div>
                    </li>
                  ))}
                </ul>
              </div>
              {selected.affected_urls.length ? (
                <div>
                  <div className="font-medium">Affected URLs</div>
                  <ul className="list-disc pl-5 text-[var(--muted)]">
                    {selected.affected_urls.map((u) => (
                      <li key={u}>{u}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {selected.affected_keywords.length ? (
                <div>
                  <div className="font-medium">Affected keywords</div>
                  <p className="text-[var(--muted)]">{selected.affected_keywords.join(", ")}</p>
                </div>
              ) : null}
              {selected.affected_topics.length ? (
                <div>
                  <div className="font-medium">Affected topics</div>
                  <p className="text-[var(--muted)]">{selected.affected_topics.join(", ")}</p>
                </div>
              ) : null}
              {selected.limitations.length ? (
                <div>
                  <div className="font-medium">Limitations</div>
                  <ul className="list-disc pl-5 text-[var(--muted)]">
                    {selected.limitations.map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="text-xs text-[var(--muted)]">
                Status: {safeText(selected.status)} · Provider: {safeText(selected.provider)} / {safeText(selected.model)} ·{" "}
                {selected.created_at ? new Date(selected.created_at).toLocaleString() : ""}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
