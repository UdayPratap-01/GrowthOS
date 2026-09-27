"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Recommendation = {
  id: string;
  title: string;
  recommendation_type: string;
  priority: string;
};

type Brief = {
  id: string;
  recommendation_id: string;
  title: string;
  brief_type: string;
  primary_keyword: string;
  secondary_keywords: string[];
  target_topic?: string | null;
  search_intent: {
    type: string;
    confidence: number;
    basis: string[];
    interpretation_note?: string;
  };
  target_url?: string | null;
  content_goal: string;
  target_audience: string;
  suggested_content_type: string;
  suggested_angle: string;
  outline: Array<{
    heading: string;
    level: string;
    purpose: string;
    key_points: string[];
  }>;
  questions_to_answer: string[];
  entities_to_cover: string[];
  internal_link_targets: string[];
  evidence_refs: Array<{ source: string; id: string; reason: string }>;
  source_recommendation_ids: string[];
  content_requirements: string[];
  seo_requirements: string[];
  limitations: string[];
  status: string;
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

export default function SeoContentBriefsPage() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [briefs, setBriefs] = useState<Brief[]>([]);
  const [selectedRecId, setSelectedRecId] = useState("");
  const [selectedBriefId, setSelectedBriefId] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const recs = await api<Recommendation[]>("/seo/recommendations?limit=50&sort=confidence");
      setRecommendations(recs.filter((r) =>
        ["content_refresh", "keyword_targeting", "topic_expansion", "content_gap", "search_intent", "competitor_gap"].includes(
          r.recommendation_type
        )
      ));
      const params = new URLSearchParams({ limit: "50" });
      if (typeFilter) params.set("brief_type", typeFilter);
      const rows = await api<Brief[]>(`/seo/content-briefs?${params.toString()}`);
      setBriefs(rows);
      if (rows.length && !selectedBriefId) setSelectedBriefId(rows[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load content briefs");
    }
  }, [typeFilter, selectedBriefId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    if (!selectedRecId) {
      setError("Select a recommendation first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/seo/content-briefs/generate", {
        method: "POST",
        body: JSON.stringify({ recommendation_id: selectedRecId }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  const selected = briefs.find((b) => b.id === selectedBriefId) ?? null;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Content Briefs"
          subtitle="AI-generated planning briefs from grounded M9.7 recommendations — not final articles."
        />
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="mb-1 block text-xs text-[var(--muted)]">Source recommendation</label>
            <select
              className="h-10 min-w-[280px] rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={selectedRecId}
              onChange={(e) => setSelectedRecId(e.target.value)}
            >
              <option value="">Select recommendation…</option>
              {recommendations.map((r) => (
                <option key={r.id} value={r.id}>
                  {safeText(r.title)} ({safeText(r.recommendation_type)})
                </option>
              ))}
            </select>
          </div>
          <Button disabled={busy || !selectedRecId} onClick={generate}>
            {busy ? "Generating…" : "Generate brief"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => void load()}>
            Refresh
          </Button>
        </div>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="mb-3">
            <select
              className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All brief types</option>
              {["content_refresh", "keyword_targeting", "topic_expansion", "content_gap", "search_intent", "competitor_gap"].map(
                (t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                )
              )}
            </select>
          </div>
          {briefs.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No content briefs yet. Generate recommendations first, then create a brief.</p>
          ) : (
            <div className="space-y-2">
              {briefs.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  onClick={() => setSelectedBriefId(b.id)}
                  className={`w-full rounded-lg border p-3 text-left text-sm transition ${
                    selectedBriefId === b.id
                      ? "border-[var(--accent)] bg-[var(--surface)]"
                      : "border-[var(--line)] hover:bg-[var(--surface)]"
                  }`}
                >
                  <div className="font-medium">{safeText(b.title)}</div>
                  <div className="text-xs text-[var(--muted)]">
                    {safeText(b.brief_type)} · {safeText(b.primary_keyword)} · {safeText(b.status)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Brief detail" subtitle="AI-generated brief guidance — verify against source evidence." />
          {!selected ? (
            <p className="text-sm text-[var(--muted)]">Select a brief to view details.</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-[var(--muted)]">AI brief guidance</div>
                <h3 className="text-lg font-medium">{safeText(selected.title)}</h3>
              </div>
              <div>
                <div className="font-medium">Source recommendation</div>
                <p className="font-mono text-xs text-[var(--muted)]">{selected.recommendation_id}</p>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div>
                  <div className="font-medium">Primary keyword</div>
                  <p>{safeText(selected.primary_keyword)}</p>
                </div>
                <div>
                  <div className="font-medium">Content type</div>
                  <p>{safeText(selected.suggested_content_type)}</p>
                </div>
              </div>
              {selected.target_topic ? (
                <div>
                  <div className="font-medium">Topic</div>
                  <p>{safeText(selected.target_topic)}</p>
                </div>
              ) : null}
              <div>
                <div className="font-medium">Search intent (AI interpretation)</div>
                <p>
                  {safeText(selected.search_intent.type)} ({Math.round(selected.search_intent.confidence * 100)}%)
                </p>
                <p className="text-xs text-[var(--muted)]">{safeText(selected.search_intent.interpretation_note)}</p>
              </div>
              {selected.target_url ? (
                <div>
                  <div className="font-medium">Target URL</div>
                  <p className="text-[var(--muted)]">{safeText(selected.target_url)}</p>
                </div>
              ) : null}
              <div>
                <div className="font-medium">Content goal</div>
                <p>{safeText(selected.content_goal)}</p>
              </div>
              <div>
                <div className="font-medium">Suggested angle</div>
                <p className="text-[var(--muted)]">{safeText(selected.suggested_angle)}</p>
              </div>
              <div>
                <div className="font-medium">Outline</div>
                <ul className="mt-1 space-y-2">
                  {selected.outline.map((s, i) => (
                    <li key={i} className="rounded border border-[var(--line)] p-2">
                      <div className="font-medium">
                        {safeText(s.level)}: {safeText(s.heading)}
                      </div>
                      <p className="text-[var(--muted)]">{safeText(s.purpose)}</p>
                      {s.key_points.length ? (
                        <ul className="mt-1 list-disc pl-5 text-xs text-[var(--muted)]">
                          {s.key_points.map((kp, j) => (
                            <li key={j}>{kp}</li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="font-medium">Source evidence refs</div>
                <ul className="mt-1 space-y-1">
                  {selected.evidence_refs.map((ref, i) => (
                    <li key={i} className="rounded border border-[var(--line)] p-2 text-xs">
                      <span className="font-mono">{safeText(ref.source)}</span> · {safeText(ref.id)}
                      <div className="text-[var(--muted)]">{safeText(ref.reason)}</div>
                    </li>
                  ))}
                </ul>
              </div>
              {selected.internal_link_targets.length ? (
                <div>
                  <div className="font-medium">Internal link targets (crawl evidence)</div>
                  <ul className="list-disc pl-5 text-[var(--muted)]">
                    {selected.internal_link_targets.map((u) => (
                      <li key={u}>{u}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="text-xs text-[var(--muted)]">
                {safeText(selected.algorithm_version)} · {safeText(selected.prompt_version)} · {safeText(selected.provider)} /{" "}
                {safeText(selected.model)}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
