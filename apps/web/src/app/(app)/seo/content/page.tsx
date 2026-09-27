"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Brief = {
  id: string;
  title: string;
  brief_type: string;
  primary_keyword: string;
  status: string;
};

type GeneratedContent = {
  id: string;
  content_brief_id: string;
  recommendation_id: string;
  title: string;
  slug: string;
  content_type: string;
  status: string;
  content: string;
  structured_sections: Array<{
    heading: string;
    level: string;
    content: string;
    subsections?: Array<{ heading: string; level: string; content: string }>;
  }>;
  primary_keyword: string;
  meta_title: string;
  meta_description: string;
  internal_link_targets: Array<{ url: string; anchor_text: string }>;
  evidence_refs: Array<{ source: string; id: string; reason: string }>;
  limitations: string[];
  word_count: number;
  provider: string;
  model: string;
  prompt_version: string;
  algorithm_version: string;
  created_at?: string;
};

type Source = {
  content_id: string;
  content_brief_id: string;
  recommendation_id: string;
  brief_snapshot: Record<string, unknown>;
  evidence_refs: Array<{ source: string; id: string; reason: string }>;
  limitations: string[];
  disclaimer: string;
};

type OnPageFinding = {
  id: string;
  finding_type: string;
  category: string;
  severity: string;
  priority: string;
  title: string;
  summary: string;
  rationale: string;
  current_value: string | null;
  expected_value: string | null;
  recommendation: string;
  evidence_refs: Array<{ source: string; id: string; reason: string }>;
  affected_section: string | null;
  suggested_change: string | null;
};

type OptimizationReport = {
  run: {
    id: string;
    status: string;
    stats: { total?: number; by_severity?: Record<string, number>; by_category?: Record<string, number> };
    limitations: string[];
    algorithm_version: string;
    prompt_version: string;
    ai_enriched: boolean;
  };
  findings: OnPageFinding[];
  disclaimer: string;
};

type SchemaArtifact = {
  id: string;
  schema_type: string;
  status: string;
  json_ld: Record<string, unknown> | null;
  validation_status: string;
  validation_errors: Array<{ code: string; message: string }>;
  validation_warnings: Array<{ code: string; message: string }>;
  eligibility_status: string;
  eligibility_reasons: string[];
  limitations: string[];
  generation_algorithm_version: string;
  validation_algorithm_version: string;
};

type SchemaReport = {
  artifacts: SchemaArtifact[];
  eligibility_summary: Array<{ schema_type: string; status: string; reasons: string[] }>;
  disclaimer: string;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function SeoContentPage() {
  const [briefs, setBriefs] = useState<Brief[]>([]);
  const [contents, setContents] = useState<GeneratedContent[]>([]);
  const [selectedBriefId, setSelectedBriefId] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimization, setOptimization] = useState<OptimizationReport | null>(null);
  const [schemaBusy, setSchemaBusy] = useState(false);
  const [schemaReport, setSchemaReport] = useState<SchemaReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const briefRows = await api<Brief[]>("/seo/content-briefs?limit=50");
      setBriefs(briefRows.filter((b) => b.status !== "archived"));
      const params = new URLSearchParams({ limit: "50" });
      if (statusFilter) params.set("status", statusFilter);
      const rows = await api<GeneratedContent[]>(`/seo/content?${params.toString()}`);
      setContents(rows);
      if (rows.length && !selectedId) setSelectedId(rows[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load content");
    }
  }, [statusFilter, selectedId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selectedId) {
      setSource(null);
      setOptimization(null);
      setSchemaReport(null);
      return;
    }
    void api<Source>(`/seo/content/${selectedId}/source`)
      .then(setSource)
      .catch(() => setSource(null));
    void api<OptimizationReport>(`/seo/content/${selectedId}/optimization`)
      .then(setOptimization)
      .catch(() => setOptimization(null));
    void api<SchemaReport>(`/seo/content/${selectedId}/schema`)
      .then(setSchemaReport)
      .catch(() => setSchemaReport(null));
  }, [selectedId]);

  async function runOptimization() {
    if (!selectedId) return;
    setOptimizing(true);
    setError(null);
    try {
      await api(`/seo/content/${selectedId}/optimize`, { method: "POST" });
      const report = await api<OptimizationReport>(`/seo/content/${selectedId}/optimization`);
      setOptimization(report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Optimization failed");
    } finally {
      setOptimizing(false);
    }
  }

  async function runSchemaGenerate() {
    if (!selectedId) return;
    setSchemaBusy(true);
    setError(null);
    try {
      await api(`/seo/content/${selectedId}/schema`, { method: "POST", body: JSON.stringify({}) });
      setSchemaReport(await api<SchemaReport>(`/seo/content/${selectedId}/schema`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Schema generation failed");
    } finally {
      setSchemaBusy(false);
    }
  }

  async function runSchemaValidate() {
    if (!selectedId) return;
    setSchemaBusy(true);
    setError(null);
    try {
      await api(`/seo/content/${selectedId}/schema/validate`, { method: "POST", body: JSON.stringify({}) });
      setSchemaReport(await api<SchemaReport>(`/seo/content/${selectedId}/schema`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Schema validation failed");
    } finally {
      setSchemaBusy(false);
    }
  }

  function copyJsonLd(artifact: SchemaArtifact) {
    if (!artifact.json_ld) return;
    void navigator.clipboard.writeText(JSON.stringify(artifact.json_ld, null, 2));
  }

  async function generate() {
    if (!selectedBriefId) {
      setError("Select a content brief first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/seo/content/generate", {
        method: "POST",
        body: JSON.stringify({ content_brief_id: selectedBriefId }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  const selected = contents.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Generated Content"
          subtitle="AI draft content from M9.8 briefs — review only, not auto-published."
        />
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="mb-1 block text-xs text-[var(--muted)]">Source content brief</label>
            <select
              className="h-10 min-w-[280px] rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={selectedBriefId}
              onChange={(e) => setSelectedBriefId(e.target.value)}
            >
              <option value="">Select brief…</option>
              {briefs.map((b) => (
                <option key={b.id} value={b.id}>
                  {safeText(b.title)} ({safeText(b.primary_keyword)})
                </option>
              ))}
            </select>
          </div>
          <Button disabled={busy || !selectedBriefId} onClick={generate}>
            {busy ? "Generating…" : "Generate content"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => void load()}>
            Refresh
          </Button>
        </div>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          Draft only. M9.9 does not publish or modify live websites.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="mb-3">
            <select
              className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All statuses</option>
              {["draft", "ready", "archived"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          {contents.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No generated content yet. Create a brief first.</p>
          ) : (
            <div className="space-y-2">
              {contents.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setSelectedId(c.id)}
                  className={`w-full rounded-lg border p-3 text-left text-sm transition ${
                    selectedId === c.id
                      ? "border-[var(--accent)] bg-[var(--surface)]"
                      : "border-[var(--line)] hover:bg-[var(--surface)]"
                  }`}
                >
                  <div className="font-medium">{safeText(c.title)}</div>
                  <div className="text-xs text-[var(--muted)]">
                    {safeText(c.content_type)} · {c.word_count} words · {safeText(c.status)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Content detail" subtitle="AI-generated draft — not fact-checked externally." />
          {!selected ? (
            <p className="text-sm text-[var(--muted)]">Select generated content to review.</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-[var(--muted)]">AI-generated content</div>
                <h3 className="text-lg font-medium">{safeText(selected.title)}</h3>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div>
                  <div className="font-medium">Meta title</div>
                  <p>{safeText(selected.meta_title)}</p>
                </div>
                <div>
                  <div className="font-medium">Meta description</div>
                  <p className="text-[var(--muted)]">{safeText(selected.meta_description)}</p>
                </div>
              </div>
              <div>
                <div className="font-medium">Primary keyword</div>
                <p>{safeText(selected.primary_keyword)}</p>
              </div>
              <div>
                <div className="font-medium">Source brief</div>
                <p className="font-mono text-xs text-[var(--muted)]">{selected.content_brief_id}</p>
              </div>
              <div>
                <div className="font-medium">Draft body</div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-[var(--line)] bg-[var(--surface)] p-3 text-xs">
                  {selected.content}
                </pre>
              </div>
              {source ? (
                <div>
                  <div className="font-medium">Source evidence refs</div>
                  <ul className="mt-1 space-y-1">
                    {source.evidence_refs.map((ref, i) => (
                      <li key={i} className="rounded border border-[var(--line)] p-2 text-xs">
                        <span className="font-mono">{safeText(ref.source)}</span> · {safeText(ref.id)}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-[var(--muted)]">{source.disclaimer}</p>
                </div>
              ) : null}
              <div className="text-xs text-[var(--muted)]">
                {safeText(selected.algorithm_version)} · {safeText(selected.prompt_version)} · {safeText(selected.provider)} /{" "}
                {safeText(selected.model)}
              </div>
              <div className="border-t border-[var(--line)] pt-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="font-medium">On-page optimization (M9.10)</div>
                  <Button variant="secondary" disabled={optimizing} onClick={() => void runOptimization()}>
                    {optimizing ? "Analyzing…" : "Run optimization"}
                  </Button>
                </div>
                <p className="mb-2 text-xs text-[var(--muted)]">
                  Review-only recommendations. Does not publish or modify live websites.
                </p>
                {!optimization ? (
                  <p className="text-sm text-[var(--muted)]">No optimization run yet.</p>
                ) : (
                  <div className="space-y-2">
                    <div className="text-xs text-[var(--muted)]">
                      {optimization.run.algorithm_version} · {optimization.run.stats.total ?? 0} findings
                      {optimization.run.ai_enriched ? " · AI suggestions included" : ""}
                    </div>
                    {optimization.findings.length === 0 ? (
                      <p className="text-sm text-green-700">No issues detected by deterministic checks.</p>
                    ) : (
                      <div className="max-h-72 space-y-2 overflow-auto">
                        {optimization.findings.map((f) => (
                          <div key={f.id} className="rounded border border-[var(--line)] p-2 text-xs">
                            <div className="flex flex-wrap gap-2">
                              <span className="rounded bg-[var(--surface)] px-1.5 py-0.5 font-mono">{f.category}</span>
                              <span className="rounded bg-[var(--surface)] px-1.5 py-0.5">{f.severity}</span>
                              <span className="rounded bg-[var(--surface)] px-1.5 py-0.5">{f.priority}</span>
                            </div>
                            <div className="mt-1 font-medium">{safeText(f.title)}</div>
                            <p className="text-[var(--muted)]">{safeText(f.summary)}</p>
                            {f.current_value ? (
                              <p>
                                <span className="font-medium">Current:</span> {safeText(f.current_value)}
                              </p>
                            ) : null}
                            <p>
                              <span className="font-medium">Recommendation:</span> {safeText(f.recommendation)}
                            </p>
                            {f.suggested_change ? (
                              <p className="mt-1 rounded bg-amber-50 p-2 text-amber-900">
                                <span className="font-medium">Suggested rewrite:</span> {safeText(f.suggested_change)}
                              </p>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    )}
                    {optimization.run.limitations.length ? (
                      <ul className="mt-2 list-disc pl-4 text-xs text-[var(--muted)]">
                        {optimization.run.limitations.map((lim, i) => (
                          <li key={i}>{safeText(lim)}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                )}
              </div>
              <div className="border-t border-[var(--line)] pt-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium">Schema JSON-LD (M9.11)</div>
                  <div className="flex gap-2">
                    <Button variant="secondary" disabled={schemaBusy} onClick={() => void runSchemaGenerate()}>
                      {schemaBusy ? "Working…" : "Generate"}
                    </Button>
                    <Button variant="secondary" disabled={schemaBusy} onClick={() => void runSchemaValidate()}>
                      Validate
                    </Button>
                  </div>
                </div>
                <p className="mb-2 text-xs text-amber-900">
                  Draft / Review Only — does not publish, inject, or modify live websites.
                </p>
                {schemaReport?.eligibility_summary?.length ? (
                  <div className="mb-2 flex flex-wrap gap-1">
                    {schemaReport.eligibility_summary.map((e) => (
                      <span
                        key={e.schema_type}
                        className="rounded border border-[var(--line)] px-1.5 py-0.5 text-xs font-mono"
                        title={e.reasons.join("; ")}
                      >
                        {e.schema_type}: {e.status}
                      </span>
                    ))}
                  </div>
                ) : null}
                {!schemaReport?.artifacts?.length ? (
                  <p className="text-sm text-[var(--muted)]">No schema artifacts yet.</p>
                ) : (
                  <div className="max-h-80 space-y-2 overflow-auto">
                    {schemaReport.artifacts.map((a) => (
                      <div key={a.id} className="rounded border border-[var(--line)] p-2 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-medium">{safeText(a.schema_type)}</span>
                          <span className="text-[var(--muted)]">{safeText(a.validation_status)}</span>
                        </div>
                        <p className="text-[var(--muted)]">Eligibility: {safeText(a.eligibility_status)}</p>
                        {a.json_ld ? (
                          <>
                            <pre className="mt-1 max-h-32 overflow-auto rounded bg-[var(--surface)] p-2 text-[10px]">
                              {JSON.stringify(a.json_ld, null, 2)}
                            </pre>
                            <Button variant="secondary" className="mt-1" onClick={() => copyJsonLd(a)}>
                              Copy JSON-LD
                            </Button>
                          </>
                        ) : null}
                        {a.validation_errors.length ? (
                          <ul className="mt-1 list-disc pl-4 text-red-700">
                            {a.validation_errors.map((e, i) => (
                              <li key={i}>{safeText(e.message)}</li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
