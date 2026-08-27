"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import { OperatorStatus } from "@/lib/operator";

const links = [
  { href: "/autopilot/recommendations", label: "Recommendations" },
  { href: "/autopilot/actions", label: "AI Actions" },
  { href: "/autopilot/reconciliation", label: "Reconciliation" },
  { href: "/autopilot/settings", label: "Safety settings" },
];

export default function AutopilotOperatorHome() {
  const [status, setStatus] = useState<OperatorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setStatus(await api<OperatorStatus>("/autopilot/operator/status"));
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error) return <EmptyState title="Operator status unavailable" description={error} />;
  if (!status) return <EmptyState title="No status" description="Insufficient data." />;

  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">Operator control</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Monitor optimization, kill switches, and provider safety. Approvals always re-evaluate policy on the server.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:bg-[var(--panel-soft)]"
          >
            {l.label}
          </Link>
        ))}
        <Link href="/autopilot" className="rounded-lg px-3 py-2 text-sm text-[var(--muted)] underline">
          Classic Autopilot
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader title="Optimization" />
          <Badge>{status.optimization_enabled ? "ENABLED" : "DISABLED"}</Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">Mode: {status.optimization_mode}</p>
        </Card>
        <Card>
          <CardHeader title="Kill switch" />
          <Badge tone={status.autonomous_kill_switch ? "danger" : "default"}>
            {status.autonomous_kill_switch ? "ON" : "OFF"}
          </Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">{status.kill_switch.effect}</p>
        </Card>
        <Card>
          <CardHeader title="Autonomous execution" />
          <Badge>{status.autonomous_execution_enabled ? "ENABLED" : "DISABLED"}</Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">Org automation: {String(status.automation_enabled)}</p>
        </Card>
        <Card>
          <CardHeader title="Usage today" />
          <p className="text-2xl font-medium">
            {status.usage.closed_loop_actions_today}/{status.usage.max_actions_per_day}
          </p>
          <p className="mt-2 text-sm text-[var(--muted)]">Closed-loop actions</p>
        </Card>
      </div>

      <Card>
        <CardHeader title="Providers" />
        <div className="grid gap-3 md:grid-cols-2">
          {Object.entries(status.providers).map(([name, p]) => (
            <div key={name} className="rounded-lg border border-[var(--border)] p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium">{name}</span>
                <Badge>{p.status}</Badge>
              </div>
              <p className="mt-1 text-[var(--muted)]">
                connected={String(p.connected)} · credentials={String(p.credentials_configured)} ·
                autonomous={String(p.autonomous_enabled)}
              </p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
