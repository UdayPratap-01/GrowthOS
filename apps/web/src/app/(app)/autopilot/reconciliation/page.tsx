"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import { AmbiguousAction } from "@/lib/operator";

type LegacyRow = {
  action_id: string;
  action_type: string;
  platform: string | null;
  external_id: string | null;
  age_seconds: number | null;
  status: string;
};

export default function ReconciliationPage() {
  const [items, setItems] = useState<AmbiguousAction[]>([]);
  const [legacy, setLegacy] = useState<LegacyRow[]>([]);
  const [stale, setStale] = useState<Array<Record<string, unknown>>>([]);
  const [reason, setReason] = useState("Operator verified in Ads Manager");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setError(null);
    try {
      const [amb, leg, st] = await Promise.all([
        api<{ items: AmbiguousAction[] }>("/autopilot/operator/actions/ambiguous"),
        api<{ items: LegacyRow[] }>("/autopilot/operator/actions/legacy-executing").catch(() => ({ items: [] })),
        api<{ items: Array<Record<string, unknown>> }>("/autopilot/operator/actions/stale-recoveries"),
      ]);
      setItems(amb.items || []);
      setLegacy(leg.items || []);
      setStale(st.items || []);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function resolve(actionId: string, resolution: string) {
    setBusy(actionId);
    try {
      await api(`/autopilot/operator/actions/${actionId}/resolve-reconciliation`, {
        method: "POST",
        body: JSON.stringify({ resolution, reason }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function recoverLegacy(actionId: string, recovery: string) {
    setBusy(actionId);
    try {
      await api(`/autopilot/operator/actions/${actionId}/legacy-recover`, {
        method: "POST",
        body: JSON.stringify({ recovery, reason }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Skeleton className="h-80 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Reconciliation</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            UNKNOWN never auto-re-executes. Resolution requires RBAC and a written reason.
          </p>
        </div>
        <Link href="/autopilot/operator" className="text-sm underline text-[var(--muted)]">
          Operator home
        </Link>
      </div>

      {error ? <EmptyState title="Error" description={error} /> : null}

      <Card>
        <CardHeader title="Resolution reason" />
        <Input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Card>

      <Card>
        <CardHeader title="Ambiguous actions" />
        {items.length === 0 ? <p className="text-sm text-[var(--muted)]">None</p> : null}
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.action_id} className="rounded-lg border border-[var(--border)] p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{item.operation}</span>
                <Badge>{item.reconciliation_state}</Badge>
              </div>
              <p className="mt-1 text-[var(--muted)]">
                {item.provider} · {item.external_id || "no external id"} · {item.ambiguous_error}
              </p>
              <p className="mt-1 text-[var(--muted)]">
                since {item.ambiguous_since || "—"} · last checked {item.last_checked_at || "—"}
              </p>
              {item.reconciliation_state === "UNKNOWN" ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" disabled={busy === item.action_id} onClick={() => resolve(item.action_id, "CONFIRM_SUCCESS")}>
                    Confirm success
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy === item.action_id}
                    onClick={() => resolve(item.action_id, "CONFIRM_NOT_APPLIED")}
                  >
                    Confirm not applied
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy === item.action_id}
                    onClick={() => resolve(item.action_id, "KEEP_UNKNOWN")}
                  >
                    Keep unknown
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title="Legacy EXECUTING (null executing_at)" />
        {legacy.length === 0 ? <p className="text-sm text-[var(--muted)]">None</p> : null}
        <div className="space-y-3">
          {legacy.map((row) => (
            <div key={row.action_id} className="rounded-lg border border-[var(--border)] p-3 text-sm">
              <p>
                {row.action_type} · age {row.age_seconds ?? "?"}s · {row.external_id || "—"}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button size="sm" disabled={busy === row.action_id} onClick={() => recoverLegacy(row.action_id, "MARK_FAILED")}>
                  Mark failed
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy === row.action_id}
                  onClick={() => recoverLegacy(row.action_id, "MARK_UNKNOWN")}
                >
                  Mark unknown
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy === row.action_id}
                  onClick={() => recoverLegacy(row.action_id, "LEAVE_EXECUTING")}
                >
                  Leave executing
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title="Stale recovery audit" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">{JSON.stringify(stale, null, 2)}</pre>
      </Card>
    </div>
  );
}
