"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";

type MonitoringStatus = {
  disclaimer: string;
  scheduler_enabled: boolean;
  open_alerts: number;
  next_scheduled_run?: string | null;
  last_run_status?: string | null;
  last_run_at?: string | null;
  config: {
    monitoring_enabled: boolean;
    site_root_url?: string | null;
    crawl_monitoring_enabled: boolean;
    search_console_monitoring_enabled: boolean;
    competitor_monitoring_enabled: boolean;
    crawl_interval_hours: number;
    search_console_interval_hours: number;
    last_crawl_at?: string | null;
    last_sync_at?: string | null;
    last_monitor_run_at?: string | null;
    last_failure_reason?: string | null;
  };
};

type MonitoringRun = {
  id: string;
  run_type: string;
  trigger: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  failure_reason?: string | null;
  alerts_generated: number;
  changes_detected: number;
  created_at: string;
};

type MonitoringAlert = {
  id: string;
  alert_type: string;
  severity: string;
  title: string;
  summary: string;
  status: string;
  created_at: string;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function SeoMonitoringPage() {
  const [status, setStatus] = useState<MonitoringStatus | null>(null);
  const [runs, setRuns] = useState<MonitoringRun[]>([]);
  const [alerts, setAlerts] = useState<MonitoringAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const [s, r, a] = await Promise.all([
        api<MonitoringStatus>("/seo/monitoring"),
        api<{ items: MonitoringRun[] }>("/seo/monitoring/runs?limit=10"),
        api<{ items: MonitoringAlert[] }>("/seo/monitoring/alerts?limit=20&status=open"),
      ]);
      setStatus(s);
      setRuns(r.items);
      setAlerts(a.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load monitoring");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleMonitoring = async (enabled: boolean) => {
    setMessage(null);
    try {
      await api("/seo/monitoring", {
        method: "PATCH",
        body: JSON.stringify({ monitoring_enabled: enabled }),
      });
      setMessage(enabled ? "Monitoring enabled" : "Monitoring disabled");
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to update configuration");
    }
  };

  const runNow = async () => {
    setRunning(true);
    setMessage(null);
    try {
      const resp = await api<{ message: string }>("/seo/monitoring/run", { method: "POST" });
      setMessage(resp.message);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Manual run failed");
    } finally {
      setRunning(false);
    }
  };

  const acknowledge = async (alertId: string) => {
    try {
      await api(`/seo/monitoring/alerts/${alertId}/acknowledge`, { method: "POST" });
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to acknowledge alert");
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader title="SEO Monitoring" subtitle="Loading monitoring status…" />
        <div className="h-24 animate-pulse rounded-lg bg-[var(--surface)]" />
      </Card>
    );
  }

  if (error || !status) {
    return (
      <Card>
        <CardHeader title="SEO Monitoring" subtitle="Unable to load monitoring" />
        <p className="text-sm text-red-600">{error ?? "Unknown error"}</p>
        <button type="button" className="mt-3 text-sm underline" onClick={() => void load()}>
          Retry
        </button>
      </Card>
    );
  }

  const cfg = status.config;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Monitoring"
          subtitle="Continuous detection and alerting — does not modify websites or execute SEO actions."
        />
        <p className="text-xs text-[var(--muted)]">{status.disclaimer}</p>
        {message ? <p className="mt-2 text-sm">{safeText(message)}</p> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded border border-[var(--line)] px-3 py-1.5 text-sm"
            onClick={() => void toggleMonitoring(!cfg.monitoring_enabled)}
          >
            {cfg.monitoring_enabled ? "Disable monitoring" : "Enable monitoring"}
          </button>
          <button
            type="button"
            className="rounded border border-[var(--line)] px-3 py-1.5 text-sm disabled:opacity-50"
            disabled={!cfg.monitoring_enabled || running}
            onClick={() => void runNow()}
          >
            {running ? "Running…" : "Run now"}
          </button>
          <Link href="/seo" className="rounded border border-[var(--line)] px-3 py-1.5 text-sm">
            SEO Dashboard
          </Link>
        </div>
      </Card>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-[var(--line)] p-4">
          <p className="text-xs uppercase text-[var(--muted)]">Status</p>
          <p className="mt-1 text-lg font-semibold">{cfg.monitoring_enabled ? "Enabled" : "Disabled"}</p>
        </div>
        <div className="rounded-lg border border-[var(--line)] p-4">
          <p className="text-xs uppercase text-[var(--muted)]">Open alerts</p>
          <p className="mt-1 text-lg font-semibold">{status.open_alerts}</p>
        </div>
        <div className="rounded-lg border border-[var(--line)] p-4">
          <p className="text-xs uppercase text-[var(--muted)]">Scheduler</p>
          <p className="mt-1 text-lg font-semibold">{status.scheduler_enabled ? "Active" : "Global scheduler off"}</p>
        </div>
        <div className="rounded-lg border border-[var(--line)] p-4">
          <p className="text-xs uppercase text-[var(--muted)]">Last run</p>
          <p className="mt-1 text-sm">{status.last_run_status ?? "None"}</p>
          {cfg.last_failure_reason ? (
            <p className="mt-1 text-xs text-red-600">Monitoring failed: {safeText(cfg.last_failure_reason)}</p>
          ) : null}
        </div>
      </section>

      <Card>
        <CardHeader title="Configuration" subtitle="Monitoring detects changes; approval remains required for actions." />
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-[var(--muted)]">Site root</dt>
            <dd>{cfg.site_root_url ?? "Not configured"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Crawl monitoring</dt>
            <dd>{cfg.crawl_monitoring_enabled ? `Every ${cfg.crawl_interval_hours}h` : "Off"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Search Console</dt>
            <dd>{cfg.search_console_monitoring_enabled ? `Every ${cfg.search_console_interval_hours}h` : "Off"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Competitor monitoring</dt>
            <dd>{cfg.competitor_monitoring_enabled ? "Enabled" : "Off"}</dd>
          </div>
        </dl>
        {!cfg.site_root_url && cfg.crawl_monitoring_enabled ? (
          <p className="mt-3 text-sm text-amber-700">Configure a site root URL to enable crawl monitoring.</p>
        ) : null}
      </Card>

      <Card>
        <CardHeader title="Open alerts" subtitle="Detected issues needing review — not automatic fixes." />
        {alerts.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No open alerts.</p>
        ) : (
          <div className="space-y-2">
            {alerts.map((alert) => (
              <div key={alert.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{safeText(alert.title)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(alert.severity)}</span>
                  <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(alert.alert_type)}</span>
                </div>
                <p className="mt-1 text-[var(--muted)]">{safeText(alert.summary)}</p>
                <button
                  type="button"
                  className="mt-2 text-xs underline"
                  onClick={() => void acknowledge(alert.id)}
                >
                  Acknowledge
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="Recent runs" subtitle="Monitoring execution history" />
        {runs.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No monitoring runs yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[var(--muted)]">
                  <th className="py-2">Type</th>
                  <th>Trigger</th>
                  <th>Status</th>
                  <th>Alerts</th>
                  <th>Changes</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="border-t border-[var(--line)]">
                    <td className="py-2">{safeText(run.run_type)}</td>
                    <td>{safeText(run.trigger)}</td>
                    <td>{safeText(run.status)}</td>
                    <td>{run.alerts_generated}</td>
                    <td>{run.changes_detected}</td>
                    <td>{safeText(run.completed_at ?? run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
