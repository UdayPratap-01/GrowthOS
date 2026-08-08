"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { MetricLineChart } from "@/components/charts/MetricLineChart";
import { api } from "@/lib/api";
import { formatCurrency, formatNumber, formatPercent } from "@/lib/utils";
import { Analytics, Client } from "@/types";

const PERIODS = [7, 30, 90] as const;

function deltaLabel(v: number | null | undefined) {
  if (v === null || v === undefined) return "Insufficient data.";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}% vs prior`;
}

export default function AnalyticsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState<string>("");
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>(30);
  const [section, setSection] = useState<"overview" | "social" | "campaigns" | "leads" | "conversions">("overview");
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const list = await api<Client[]>("/clients");
      setClients(list);
    })();
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const path = clientId
          ? `/clients/${clientId}/analytics?period_days=${period}`
          : `/analytics?period_days=${period}`;
        setData(await api<Analytics>(path));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load analytics");
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId, period]);

  const chartData = useMemo(() => {
    if (!data) return {};
    const map = (key: string) =>
      (data.series[key] || []).map((p) => ({
        date: String(p.date).slice(5),
        value: p.value,
      }));
    return {
      leads: map("leads"),
      spend: map("spend"),
      cpl: map("cpl"),
      ctr: map("ctr"),
      conversion_rate: map("conversion_rate"),
    };
  }, [data]);

  if (loading && !data) return <Skeleton className="h-80 w-full" />;
  if (error) return <EmptyState title="Analytics unavailable" description={error} />;
  if (!data) return <EmptyState title="No analytics" description="Insufficient data." />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Analytics</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Social, campaigns, leads, and conversions with period comparison.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.demo_mode ? <Badge tone="demo">Demo Data</Badge> : null}
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.business_name}</option>
            ))}
          </Select>
          <div className="flex gap-1 rounded-lg border border-[var(--line)] bg-white p-1">
            {PERIODS.map((p) => (
              <Button key={p} size="sm" variant={period === p ? "primary" : "ghost"} onClick={() => setPeriod(p)}>
                {p}d
              </Button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex gap-2 overflow-x-auto">
        {(["overview", "social", "campaigns", "leads", "conversions"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSection(s)}
            className={`rounded-full px-3 py-1.5 text-sm capitalize ${
              section === s ? "bg-[var(--panel)] text-white" : "bg-white text-[var(--muted)]"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {data.insufficient_data.length ? (
        <Card>
          <p className="text-sm text-[var(--muted)]">
            Gaps: {data.insufficient_data.join(" · ")}
          </p>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Spend", formatCurrency(data.current.spend), data.deltas.spend],
          ["Leads", formatNumber(data.current.leads), data.deltas.leads],
          ["CPL", data.current.cpl != null ? formatCurrency(data.current.cpl) : "Insufficient data.", data.deltas.cpl],
          ["CVR", data.current.conversion_rate != null ? formatPercent(data.current.conversion_rate) : "Insufficient data.", data.deltas.conversion_rate],
        ].map(([label, value, delta]) => (
          <Card key={label as string}>
            <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</div>
            <div className="mt-2 font-display text-2xl">{value}</div>
            <div className="mt-1 text-xs text-[var(--muted)]">{deltaLabel(delta as number | null)}</div>
          </Card>
        ))}
      </div>

      {(section === "overview" || section === "leads") && (
        <Card>
          <CardHeader title="Leads over time" subtitle={`Current ${period}d vs prior ${period}d`} />
          <MetricLineChart data={chartData.leads || []} />
        </Card>
      )}
      {(section === "overview" || section === "campaigns") && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Spend over time" />
            <MetricLineChart data={chartData.spend || []} color="#0f1720" />
          </Card>
          <Card>
            <CardHeader title="CPL over time" />
            <MetricLineChart data={chartData.cpl || []} color="#b45309" />
          </Card>
        </div>
      )}
      {(section === "overview" || section === "social") && (
        <Card>
          <CardHeader title="CTR over time" />
          <MetricLineChart data={chartData.ctr || []} />
          <div className="mt-4 grid gap-3 sm:grid-cols-3 text-sm">
            <div className="rounded-xl bg-[var(--surface-2)] p-3">Impressions<br /><span className="text-lg font-medium">{formatNumber(data.current.impressions)}</span></div>
            <div className="rounded-xl bg-[var(--surface-2)] p-3">Clicks<br /><span className="text-lg font-medium">{formatNumber(data.current.clicks)}</span></div>
            <div className="rounded-xl bg-[var(--surface-2)] p-3">CTR<br /><span className="text-lg font-medium">{data.current.ctr != null ? formatPercent(data.current.ctr) : "Insufficient data."}</span></div>
          </div>
        </Card>
      )}
      {(section === "overview" || section === "conversions") && (
        <Card>
          <CardHeader title="Conversion rate over time" />
          <MetricLineChart data={chartData.conversion_rate || []} color="#0369a1" />
        </Card>
      )}

      {(section === "overview" || section === "campaigns") && (
        <Card>
          <CardHeader title="Campaign performance" subtitle="From available campaign records only." />
          {data.campaign_performance.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Insufficient data.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="text-xs uppercase text-[var(--muted)]">
                  <tr>
                    <th className="px-2 py-2">Campaign</th>
                    <th className="px-2 py-2">Spend</th>
                    <th className="px-2 py-2">Leads</th>
                    <th className="px-2 py-2">CPL</th>
                    <th className="px-2 py-2">CTR</th>
                    <th className="px-2 py-2">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {data.campaign_performance.map((c) => (
                    <tr key={c.id} className="border-t border-[var(--line)]">
                      <td className="px-2 py-3">{c.name}<div className="text-xs text-[var(--muted)]">{c.platform}</div></td>
                      <td className="px-2 py-3">{formatCurrency(c.spend)}</td>
                      <td className="px-2 py-3">{c.leads || "—"}</td>
                      <td className="px-2 py-3">{c.cpl != null ? formatCurrency(c.cpl) : "Insufficient data."}</td>
                      <td className="px-2 py-3">{c.ctr != null ? formatPercent(c.ctr) : "Insufficient data."}</td>
                      <td className="px-2 py-3"><Badge tone={c.data_source === "demo" ? "demo" : "default"}>{c.data_source}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {(section === "overview" || section === "social") && clientId && (
        <Card>
          <CardHeader title="Content performance" />
          {data.content_performance.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Insufficient data.</p>
          ) : (
            <div className="space-y-2">
              {data.content_performance.map((p) => (
                <div key={p.id} className="rounded-xl border border-[var(--line)] p-3 text-sm">
                  <div className="font-medium">{p.platform} · {p.content_type}</div>
                  <div className="text-[var(--muted)]">{p.hook}</div>
                  <div className="mt-1 text-xs">
                    {p.impressions != null
                      ? `${formatNumber(p.impressions)} impr · ${formatNumber(p.engagement || 0)} eng · CTR ${p.ctr ?? "—"}`
                      : p.note || "Insufficient data."}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {(section === "leads") && (
        <Card>
          <CardHeader title="Lead funnel (CRM)" />
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.sections.leads?.funnel || {}).map(([stage, count]) => (
              <div key={stage} className="rounded-xl bg-[var(--surface-2)] px-3 py-2 text-sm">
                <span className="capitalize">{stage}</span>: <strong>{count}</strong>
              </div>
            ))}
            {!Object.keys(data.sections.leads?.funnel || {}).length ? (
              <p className="text-sm text-[var(--muted)]">Insufficient data.</p>
            ) : null}
          </div>
        </Card>
      )}
    </div>
  );
}
