"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type Dashboard = {
  disclaimer: string;
  overview: {
    latest_crawl_id?: string | null;
    latest_crawl_status?: string | null;
    pages_crawled: number;
    technical_findings: number;
    critical_high_findings: number;
    keyword_opportunities: number;
    topic_clusters: number;
    content_gaps: number;
    recommendations: number;
    content_briefs: number;
    generated_content: number;
    internal_link_opportunities: number;
    schema_artifacts: number;
    pending_actions: number;
  };
  technical: Panel & {
    crawl_id?: string | null;
    root_url?: string | null;
    total_findings: number;
    by_severity: Record<string, number>;
    affected_pages: number;
  };
  search_console: Panel & {
    connected: boolean;
    clicks?: number | null;
    impressions?: number | null;
    ctr?: number | null;
    opportunity_count: number;
  };
  keywords: Panel & { total_opportunities: number; high_priority_count: number };
  topics: Panel & { total_topics: number };
  competitors: Panel & { active_competitors: number; total_gaps: number };
  content: Panel & {
    briefs_count: number;
    generated_count: number;
    content_with_internal_links: number;
  };
  on_page: Panel & { optimization_runs: number; total_findings: number };
  schema_panel: Panel & { artifact_count: number; finding_count: number };
  internal_links: Panel & {
    total_opportunities: number;
    high_confidence_count: number;
    suggested_count: number;
  };
  actions: Panel & {
    pending: number;
    review_only: number;
    recent_pending: Array<{ id: string; description: string; capability: string }>;
  };
  attention: Panel & {
    items: Array<{
      source: string;
      title: string;
      reason: string;
      severity_or_priority?: string | null;
      link_path?: string | null;
    }>;
  };
  monitoring: Panel & {
    monitoring_enabled: boolean;
    scheduler_enabled: boolean;
    open_alerts: number;
    last_monitor_run_at?: string | null;
    last_failure_reason?: string | null;
  };
};

type Panel = {
  available: boolean;
  empty_message?: string | null;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

function MetricCard({
  label,
  value,
  href,
  sub,
}: {
  label: string;
  value: string | number;
  href?: string;
  sub?: string;
}) {
  const inner = (
    <div className="rounded-lg border border-[var(--line)] p-4">
      <p className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {sub ? <p className="mt-1 text-xs text-[var(--muted)]">{sub}</p> : null}
    </div>
  );
  if (href) {
    return (
      <Link href={href} className="block transition hover:border-[var(--accent)]">
        {inner}
      </Link>
    );
  }
  return inner;
}

export default function SeoDashboardPage() {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const data = await api<Dashboard>("/seo/dashboard");
      setDash(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader title="SEO Dashboard" subtitle="Loading persisted SEO state…" />
          <div className="h-24 animate-pulse rounded-lg bg-[var(--surface)]" />
        </Card>
      </div>
    );
  }

  if (error || !dash) {
    return (
      <Card>
        <CardHeader title="SEO Dashboard" subtitle="Unable to load dashboard" />
        <p className="text-sm text-red-600">{error ?? "Unknown error"}</p>
        <button type="button" className="mt-3 text-sm underline" onClick={() => void load()}>
          Retry
        </button>
      </Card>
    );
  }

  const o = dash.overview;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Dashboard"
          subtitle="Unified view of persisted SEO data — read-only aggregation, no automatic actions."
        />
        <p className="text-xs text-[var(--muted)]">{dash.disclaimer}</p>
      </Card>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">Overview</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Latest crawl"
            value={o.latest_crawl_status ?? "None"}
            href="/seo/crawler"
            sub={o.pages_crawled ? `${o.pages_crawled} pages` : "Run a crawl"}
          />
          <MetricCard
            label="Technical findings"
            value={o.technical_findings}
            href="/seo/crawler"
            sub={o.critical_high_findings ? `${o.critical_high_findings} high/critical` : undefined}
          />
          <MetricCard label="Keyword opportunities" value={o.keyword_opportunities} href="/seo/keywords" />
          <MetricCard label="Pending SEO actions" value={o.pending_actions} href="/seo/content" />
          <MetricCard label="Topic clusters" value={o.topic_clusters} href="/seo/topics" />
          <MetricCard label="Content gaps" value={o.content_gaps} href="/seo/content-gaps" />
          <MetricCard label="Recommendations" value={o.recommendations} href="/seo/recommendations" />
          <MetricCard label="Internal links" value={o.internal_link_opportunities} href="/seo/content" />
          <MetricCard
            label="Monitoring alerts"
            value={dash.monitoring.open_alerts}
            href="/seo/monitoring"
            sub={dash.monitoring.monitoring_enabled ? "Monitoring enabled" : "Monitoring disabled"}
          />
        </div>
      </section>

      {dash.monitoring.last_failure_reason ? (
        <Card>
          <CardHeader title="Monitoring status" subtitle="Latest monitoring failure detected" />
          <p className="text-sm text-red-600">{safeText(dash.monitoring.last_failure_reason)}</p>
          <Link href="/seo/monitoring" className="mt-2 inline-block text-sm underline">
            View monitoring
          </Link>
        </Card>
      ) : null}

      {dash.attention.available && dash.attention.items.length > 0 ? (
        <Card>
          <CardHeader title="Needs attention" subtitle="Items flagged from existing priority/severity signals" />
          <div className="space-y-2">
            {dash.attention.items.map((item, idx) => (
              <div key={`${item.source}-${idx}`} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{safeText(item.title)}</span>
                  {item.severity_or_priority ? (
                    <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">
                      {safeText(item.severity_or_priority)}
                    </span>
                  ) : null}
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(item.source)}</span>
                </div>
                <p className="mt-1 text-[var(--muted)]">{safeText(item.reason)}</p>
                {item.link_path ? (
                  <Link href={item.link_path} className="mt-1 inline-block text-xs underline">
                    View details
                  </Link>
                ) : null}
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Technical SEO" subtitle={dash.technical.available ? safeText(dash.technical.root_url) : "Not available"} />
          {dash.technical.available ? (
            <div className="text-sm">
              <p>{dash.technical.total_findings} findings · {dash.technical.affected_pages} pages affected</p>
              <Link href="/seo/crawler" className="mt-2 inline-block text-xs underline">
                Open crawler
              </Link>
            </div>
          ) : (
            <p className="text-sm text-[var(--muted)]">{dash.technical.empty_message}</p>
          )}
        </Card>

        <Card>
          <CardHeader title="Search Console" subtitle={dash.search_console.connected ? "Connected" : "Not connected"} />
          {dash.search_console.available ? (
            <div className="text-sm">
              <p>
                Clicks: {dash.search_console.clicks ?? "—"} · Impressions:{" "}
                {dash.search_console.impressions ?? "—"}
              </p>
              <p className="text-[var(--muted)]">{dash.search_console.opportunity_count} opportunities</p>
              <Link href="/seo/search-console" className="mt-2 inline-block text-xs underline">
                Open Search Console
              </Link>
            </div>
          ) : (
            <p className="text-sm text-[var(--muted)]">{dash.search_console.empty_message}</p>
          )}
        </Card>

        <Card>
          <CardHeader title="Content" subtitle={`${dash.content.briefs_count} briefs · ${dash.content.generated_count} drafts`} />
          {dash.content.available ? (
            <div className="text-sm">
              <p>
                {dash.content.content_with_internal_links} with internal links · optimization & schema tracked per
                content item
              </p>
              <Link href="/seo/content" className="mt-2 inline-block text-xs underline">
                Open content workspace
              </Link>
            </div>
          ) : (
            <p className="text-sm text-[var(--muted)]">{dash.content.empty_message}</p>
          )}
        </Card>

        <Card>
          <CardHeader title="SEO Actions" subtitle="Explicit approval required — review/export only" />
          <div className="text-sm">
            <p>
              Pending: {dash.actions.pending} · Review-only: {dash.actions.review_only}
            </p>
            {dash.actions.recent_pending.length > 0 ? (
              <ul className="mt-2 space-y-1 text-[var(--muted)]">
                {dash.actions.recent_pending.map((a) => (
                  <li key={a.id}>
                    {safeText(a.description)} ({safeText(a.capability)})
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="mt-2 text-xs text-[var(--muted)]">
              Review/export only — no live site writer configured
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Schema" subtitle={`${dash.schema_panel.artifact_count} artifacts`} />
          {dash.schema_panel.available ? (
            <p className="text-sm">{dash.schema_panel.finding_count} schema findings</p>
          ) : (
            <p className="text-sm text-[var(--muted)]">{dash.schema_panel.empty_message}</p>
          )}
        </Card>

        <Card>
          <CardHeader title="Internal links" subtitle={`${dash.internal_links.total_opportunities} opportunities`} />
          {dash.internal_links.available ? (
            <p className="text-sm">
              {dash.internal_links.suggested_count} suggested · {dash.internal_links.high_confidence_count} high
              confidence
            </p>
          ) : (
            <p className="text-sm text-[var(--muted)]">{dash.internal_links.empty_message}</p>
          )}
        </Card>
      </section>
    </div>
  );
}
