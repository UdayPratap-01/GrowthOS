"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { formatCurrency, formatNumber } from "@/lib/utils";
import { Client, Report } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export default function ReportsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [reports, setReports] = useState<Report[]>([]);
  const [active, setActive] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const list = await api<Client[]>("/clients");
      setClients(list);
      if (list[0]) setClientId(list[0].id);
      setLoading(false);
    })();
  }, []);

  useEffect(() => {
    if (!clientId) return;
    (async () => {
      setError(null);
      try {
        const list = await api<Report[]>(`/clients/${clientId}/reports`);
        setReports(list);
        setActive(list[0] || null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load reports");
      }
    })();
  }, [clientId]);

  async function generate() {
    if (!clientId) return;
    setBusy(true);
    try {
      const report = await api<Report>(`/clients/${clientId}/reports/generate`, {
        method: "POST",
        body: JSON.stringify({ period_days: 7 }),
      });
      setReports((prev) => [report, ...prev]);
      setActive(report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Report generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function exportPdf() {
    if (!active || !clientId) return;
    const token = localStorage.getItem("growthos_access_token");
    const res = await fetch(`${API_URL}/clients/${clientId}/reports/${active.id}/pdf`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      setError("PDF export failed");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `growthos-report-${active.id}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!clients.length) {
    return <EmptyState title="Add a client first" description="Weekly reports are client-scoped." />;
  }

  const metrics = active?.content.key_metrics || {};

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Reports</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">AI weekly reports with exportable PDF.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select className="w-52" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.business_name}</option>)}
          </Select>
          <Button onClick={generate} disabled={busy}>{busy ? "Generating..." : "Generate weekly report"}</Button>
          {active ? <Button variant="secondary" onClick={exportPdf}>Export PDF</Button> : null}
        </div>
      </div>

      {error ? <EmptyState title="Reports error" description={error} /> : null}

      {!active ? (
        <EmptyState title="No reports yet" description="Generate a weekly report from available analytics." actionLabel="Generate" onAction={generate} />
      ) : (
        <div className="grid gap-4 xl:grid-cols-[240px_1fr]">
          <Card>
            <CardHeader title="History" />
            <div className="space-y-2">
              {reports.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setActive(r)}
                  className={`w-full rounded-xl border px-3 py-2 text-left text-sm ${
                    active.id === r.id ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--line)]"
                  }`}
                >
                  <div className="font-medium">{r.period_start} → {r.period_end}</div>
                  <div className="text-xs text-[var(--muted)]">{r.status}</div>
                </button>
              ))}
            </div>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardHeader
                title={active.title}
                subtitle={`${active.period_start} to ${active.period_end}`}
                action={active.content.data_source === "demo" ? <Badge tone="demo">Demo Data</Badge> : undefined}
              />
              <p className="text-sm leading-relaxed">{active.content.executive_summary || "Insufficient data."}</p>
            </Card>

            <div className="grid gap-4 sm:grid-cols-3">
              <Card><div className="text-xs text-[var(--muted)]">Spend</div><div className="text-xl">{metrics.spend != null ? formatCurrency(metrics.spend) : "Insufficient data."}</div></Card>
              <Card><div className="text-xs text-[var(--muted)]">Leads</div><div className="text-xl">{metrics.leads != null ? formatNumber(metrics.leads) : "Insufficient data."}</div></Card>
              <Card><div className="text-xs text-[var(--muted)]">CPL</div><div className="text-xl">{metrics.cpl != null ? formatCurrency(metrics.cpl) : "Insufficient data."}</div></Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Growth" />
                <ul className="list-disc pl-5 text-sm">{(active.content.growth || ["Insufficient data."]).map((g) => <li key={g}>{g}</li>)}</ul>
              </Card>
              <Card>
                <CardHeader title="Declines" />
                <ul className="list-disc pl-5 text-sm">{(active.content.declines || ["Insufficient data."]).map((g) => <li key={g}>{g}</li>)}</ul>
              </Card>
            </div>

            <Card>
              <CardHeader title="AI insights" />
              <ul className="list-disc pl-5 text-sm">{(active.content.ai_insights || ["Insufficient data."]).map((g) => <li key={g}>{g}</li>)}</ul>
              <p className="mt-4 text-sm"><span className="font-medium">Next week:</span> {active.content.next_week_strategy || "Insufficient data."}</p>
              {(active.content.insufficient_data || []).length ? (
                <p className="mt-3 text-xs text-[var(--muted)]">Gaps: {active.content.insufficient_data?.join(" · ")}</p>
              ) : null}
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Top content" />
                {(active.content.top_content || []).length ? (
                  <div className="space-y-2 text-sm">
                    {active.content.top_content!.map((item, idx) => (
                      <div key={idx} className="rounded-lg border border-[var(--line)] p-2">
                        {String(item.platform || "")} · {String(item.hook || "Insufficient data.")}
                      </div>
                    ))}
                  </div>
                ) : <p className="text-sm text-[var(--muted)]">Insufficient data.</p>}
              </Card>
              <Card>
                <CardHeader title="Top campaigns" />
                {(active.content.top_campaigns || []).length ? (
                  <div className="space-y-2 text-sm">
                    {active.content.top_campaigns!.map((item, idx) => (
                      <div key={idx} className="rounded-lg border border-[var(--line)] p-2">
                        {String(item.name || "")} · spend {item.spend != null ? formatCurrency(Number(item.spend)) : "—"}
                      </div>
                    ))}
                  </div>
                ) : <p className="text-sm text-[var(--muted)]">Insufficient data.</p>}
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
