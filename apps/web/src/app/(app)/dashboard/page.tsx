"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { formatCurrency, formatNumber, formatPercent } from "@/lib/utils";
import { Client, Dashboard } from "@/types";

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<Dashboard | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [dash, clientList] = await Promise.all([
          api<Dashboard>("/dashboard"),
          api<Client[]>("/clients"),
        ]);
        setData(dash);
        setClients(clientList);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load dashboard");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function generateTodayStrategy() {
    if (!clients[0]) {
      router.push("/clients");
      return;
    }
    setGenerating(true);
    try {
      await api(`/clients/${clients[0].id}/strategies/generate`, {
        method: "POST",
        body: JSON.stringify({ title: "Today's Strategy" }),
      });
      router.push(`/clients/${clients[0].id}?tab=strategy`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Strategy generation failed");
    } finally {
      setGenerating(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return <EmptyState title="Dashboard unavailable" description={error} />;
  }

  if (!data) {
    return <EmptyState title="No dashboard data" description="Seed demo data or create your first client." />;
  }

  const kpis = [
    { label: "Total clients", value: formatNumber(data.kpis.total_clients) },
    { label: "Total leads", value: formatNumber(data.kpis.total_leads) },
    { label: "Total ad spend", value: formatCurrency(data.kpis.total_ad_spend) },
    { label: "Estimated revenue", value: formatCurrency(data.kpis.estimated_revenue) },
    { label: "Average CPL", value: data.kpis.average_cpl ? formatCurrency(data.kpis.average_cpl) : "Insufficient data." },
    { label: "Conversion rate", value: data.kpis.conversion_rate ? formatPercent(data.kpis.conversion_rate) : "Insufficient data." },
    { label: "Marketing health", value: data.kpis.marketing_health_score != null ? `${data.kpis.marketing_health_score}/100` : "Insufficient data." },
  ];

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-[var(--ink)]">Dashboard</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Agency-wide performance and AI priorities.</p>
        </div>
        <div className="flex items-center gap-2">
          {data.demo_mode || data.kpis.data_source !== "live" ? (
            <Badge tone="demo">
              {data.kpis.data_source === "mixed" ? "Mixed data (seed + live)" : "Demo Data"}
            </Badge>
          ) : (
            <Badge tone="success">Live data</Badge>
          )}
          <Button onClick={generateTodayStrategy} disabled={generating}>
            {generating ? "Generating..." : "Generate Today’s Strategy"}
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi, idx) => (
          <Card key={kpi.label} className="animate-rise" style={{ animationDelay: `${idx * 40}ms` } as React.CSSProperties}>
            <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{kpi.label}</div>
            <div className="mt-2 font-display text-2xl text-[var(--ink)]">{kpi.value}</div>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="AI Priorities" subtitle="Ranked recommendations from available evidence." />
          <div className="space-y-3">
            {data.ai_priorities.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">No pending AI priorities.</p>
            ) : (
              data.ai_priorities.map((item) => (
                <div key={item.id} className="rounded-xl border border-[var(--line)] bg-[var(--surface-2)] p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <Badge tone={item.priority}>{item.priority} priority</Badge>
                    {item.client_name ? <span className="text-xs text-[var(--muted)]">{item.client_name}</span> : null}
                  </div>
                  <div className="font-medium text-[var(--ink)]">{item.title}</div>
                  <p className="mt-1 text-sm text-[var(--muted)]">
                    <span className="font-medium text-[var(--accent-ink)]">AI recommendation:</span> {item.recommendation}
                  </p>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Pending approvals" subtitle="Strategy actions awaiting review." />
          <div className="space-y-3">
            {data.pending_approvals.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">No pending approvals.</p>
            ) : (
              data.pending_approvals.map((item) => (
                <Link
                  key={item.id}
                  href={`/clients/${item.client_id}?tab=strategy`}
                  className="block rounded-xl border border-[var(--line)] p-3 transition hover:border-[var(--accent)]"
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <Badge tone={item.priority}>{item.priority}</Badge>
                    <span className="text-xs text-[var(--muted)]">{item.client_name}</span>
                  </div>
                  <div className="text-sm text-[var(--ink)]">{item.title}</div>
                </Link>
              ))
            )}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Client performance" subtitle="Spend and lead efficiency by client." />
          <div className="space-y-3">
            {data.client_performance.map((c) => (
              <Link
                key={c.client_id}
                href={`/clients/${c.client_id}`}
                className="flex items-center justify-between rounded-xl border border-[var(--line)] px-4 py-3 transition hover:border-[var(--accent)]"
              >
                <div>
                  <div className="font-medium text-[var(--ink)]">{c.business_name}</div>
                  <div className="text-xs text-[var(--muted)]">{c.industry || "—"} · {c.data_source === "demo" ? "Demo Data" : "Live"}</div>
                </div>
                <div className="text-right text-sm">
                  <div>{formatCurrency(c.spend)} spend</div>
                  <div className="text-[var(--muted)]">{c.leads} leads · CPL {c.cpl ? formatCurrency(c.cpl) : "—"}</div>
                </div>
              </Link>
            ))}
          </div>
        </Card>

        <Card>
          <CardHeader title="Recent AI recommendations" />
          <div className="space-y-3">
            {data.recent_recommendations.map((r) => (
              <div key={r.id} className="rounded-xl border border-[var(--line)] p-3">
                <div className="font-medium">{r.title}</div>
                <p className="mt-1 text-sm text-[var(--muted)]">{r.recommendation}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
