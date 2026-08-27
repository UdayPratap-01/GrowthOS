"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";

type Detail = {
  action: Record<string, unknown>;
  lifecycle: {
    recommendation_id?: string;
    closed_loop?: boolean;
    decision?: string;
    autonomy_mode?: string;
    risk?: string;
    policy_checks?: Array<{ name: string; passed: boolean; detail: string }>;
    reconciliation?: Record<string, unknown>;
  };
  executions: Array<Record<string, unknown>>;
  audit_events: Array<Record<string, unknown>>;
};

export default function ActionDetailPage() {
  const params = useParams<{ actionId: string }>();
  const actionId = params.actionId;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!actionId) return;
    (async () => {
      try {
        setDetail(await api<Detail>(`/autopilot/operator/actions/${actionId}/detail`));
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    })();
  }, [actionId]);

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error) return <EmptyState title="Action detail unavailable" description={error} />;
  if (!detail) return null;

  const action = detail.action;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Action detail</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Lifecycle and policy checks — no secrets.</p>
        </div>
        <Link href="/autopilot/actions" className="text-sm underline text-[var(--muted)]">
          Back
        </Link>
      </div>

      <Card>
        <CardHeader
          title={String(action.description || action.action_type)}
          action={<Badge>{String(action.status)}</Badge>}
        />
        <div className="grid gap-2 text-sm md:grid-cols-2">
          <p>Risk: {detail.lifecycle.risk || String(action.risk_level)}</p>
          <p>Autonomy: {detail.lifecycle.autonomy_mode || "—"}</p>
          <p>Decision: {detail.lifecycle.decision || "—"}</p>
          <p>Recommendation: {detail.lifecycle.recommendation_id || "—"}</p>
        </div>
      </Card>

      <Card>
        <CardHeader title="Policy checks" />
        <div className="space-y-2 text-sm">
          {(detail.lifecycle.policy_checks || []).length === 0 ? (
            <p className="text-[var(--muted)]">No structured policy checks on payload.</p>
          ) : (
            detail.lifecycle.policy_checks?.map((c, i) => (
              <div key={`${c.name}-${i}`} className="flex justify-between gap-3 border-b border-[var(--line)] py-2">
                <span>{c.name}</span>
                <Badge>{c.passed ? "PASS" : "BLOCKED"}</Badge>
              </div>
            ))
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Reconciliation" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">
          {JSON.stringify(detail.lifecycle.reconciliation || {}, null, 2)}
        </pre>
      </Card>

      <Card>
        <CardHeader title="Executions" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">
          {JSON.stringify(detail.executions || [], null, 2)}
        </pre>
      </Card>

      <Card>
        <CardHeader title="Audit events" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">
          {JSON.stringify(detail.audit_events || [], null, 2)}
        </pre>
      </Card>
    </div>
  );
}
