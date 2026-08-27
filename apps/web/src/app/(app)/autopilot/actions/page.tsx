"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import { AIAction } from "@/types";

export default function AutopilotActionsPage() {
  const [items, setItems] = useState<AIAction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setItems(await api<AIAction[]>("/autopilot/activity"));
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <Skeleton className="h-80 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Actions</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Structured actions from the existing execution pipeline.</p>
        </div>
        <Link href="/autopilot/operator" className="text-sm underline text-[var(--muted)]">
          Operator home
        </Link>
      </div>
      {error ? <EmptyState title="Error" description={error} /> : null}
      {items.length === 0 ? <EmptyState title="No actions" description="No AI actions yet." /> : null}
      <div className="space-y-3">
        {items.map((a) => {
          const recon = (a.result || {}).reconciliation as { state?: string } | undefined;
          return (
            <Card key={a.id}>
              <CardHeader
                title={a.description || a.action_type}
                action={<Badge>{a.status}</Badge>}
              />
              <div className="grid gap-2 text-sm md:grid-cols-3">
                <p>Type: {a.action_type}</p>
                <p>Provider: {a.platform || "—"}</p>
                <p>Campaign: {a.target_id || "—"}</p>
                <p>Risk: {a.risk_level}</p>
                <p>Retries: {a.retry_count}</p>
                <p>Reconciliation: {recon?.state || "—"}</p>
                <p>Created: {new Date(a.created_at).toLocaleString()}</p>
                <p>Error: {a.error || "—"}</p>
                <p>Agent: {a.agent}</p>
              </div>
              <Link
                href={`/autopilot/actions/${a.id}`}
                className="mt-3 inline-block text-sm underline text-[var(--muted)]"
              >
                Open detail
              </Link>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
