"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type ReportSummary = {
  id: string;
  period_start: string;
  period_end: string;
  status: string;
  summary: string;
  generated_at?: string | null;
  limitations: string[];
};

type ReportDetail = ReportSummary & {
  report_payload: {
    disclaimer?: string;
    sections?: Record<string, unknown>;
  };
  disclaimer?: string;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function SeoReportsPage() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [selected, setSelected] = useState<ReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const data = await api<{ items: ReportSummary[] }>("/seo/reports?limit=20");
      setReports(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load reports");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const generate = async () => {
    setGenerating(true);
    setMessage(null);
    try {
      const resp = await api<{ message: string; report_id: string }>("/seo/reports/generate", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setMessage(resp.message);
      await load();
      const detail = await api<ReportDetail>(`/seo/reports/${resp.report_id}`);
      setSelected(detail);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const openReport = async (id: string) => {
    try {
      const detail = await api<ReportDetail>(`/seo/reports/${id}`);
      setSelected(detail);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to load report");
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader title="SEO Weekly Reports" subtitle="Loading report history…" />
        <div className="h-24 animate-pulse rounded-lg bg-[var(--surface)]" />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Weekly Reports"
          subtitle="Structured weekly reports from persisted GrowthOS data — read-only, no autonomous execution."
        />
        {message ? <p className="mt-2 text-sm">{safeText(message)}</p> : null}
        {error ? <p className="mt-2 text-sm text-red-600">{safeText(error)}</p> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded border border-[var(--line)] px-3 py-1.5 text-sm disabled:opacity-50"
            disabled={generating}
            onClick={() => void generate()}
          >
            {generating ? "Generating…" : "Generate weekly report"}
          </button>
          <Link href="/seo" className="rounded border border-[var(--line)] px-3 py-1.5 text-sm">
            SEO Dashboard
          </Link>
          <Link href="/seo/monitoring" className="rounded border border-[var(--line)] px-3 py-1.5 text-sm">
            Monitoring
          </Link>
        </div>
      </Card>

      <Card>
        <CardHeader title="Report history" subtitle="UTC weekly periods (Monday–Sunday)" />
        {reports.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No reports yet. Generate your first weekly report.</p>
        ) : (
          <div className="space-y-2">
            {reports.map((r) => (
              <button
                key={r.id}
                type="button"
                className="block w-full rounded-lg border border-[var(--line)] p-3 text-left text-sm hover:border-[var(--accent)]"
                onClick={() => void openReport(r.id)}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {r.period_start} → {r.period_end}
                  </span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(r.status)}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[var(--muted)]">{safeText(r.summary)}</p>
              </button>
            ))}
          </div>
        )}
      </Card>

      {selected ? (
        <Card>
          <CardHeader
            title={`Report ${selected.period_start} – ${selected.period_end}`}
            subtitle={safeText(selected.disclaimer || selected.report_payload?.disclaimer)}
          />
          <p className="whitespace-pre-wrap text-sm">{safeText(selected.summary)}</p>

          {selected.limitations.length > 0 ? (
            <section className="mt-4">
              <h3 className="text-sm font-semibold">Data limitations</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-[var(--muted)]">
                {selected.limitations.map((l, i) => (
                  <li key={i}>{safeText(l)}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {selected.report_payload?.sections ? (
            <section className="mt-6 space-y-4">
              {Object.entries(selected.report_payload.sections).map(([key, section]) => (
                <div key={key} className="rounded-lg border border-[var(--line)] p-3">
                  <h4 className="text-sm font-semibold capitalize">{key.replace(/_/g, " ")}</h4>
                  <pre className="mt-2 max-h-64 overflow-auto text-xs text-[var(--muted)]">
                    {JSON.stringify(section, null, 2)}
                  </pre>
                </div>
              ))}
            </section>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
