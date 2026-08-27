"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import { OperatorStatus } from "@/lib/operator";

export default function AutopilotSafetySettingsPage() {
  const [status, setStatus] = useState<OperatorStatus | null>(null);
  const [policies, setPolicies] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [s, p] = await Promise.all([
          api<OperatorStatus>("/autopilot/operator/status"),
          api<Record<string, unknown>>("/autopilot/optimization/policies"),
        ]);
        setStatus(s);
        setPolicies(p);
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error) return <EmptyState title="Settings unavailable" description={error} />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Safety settings</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Kill switches and canary allowlists are environment-controlled. Org autonomy is edited under Autopilot settings.
          </p>
        </div>
        <Link href="/autopilot/operator" className="text-sm underline text-[var(--muted)]">
          Operator home
        </Link>
      </div>

      <Card>
        <CardHeader title="Effective operator status" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">
          {JSON.stringify(status, null, 2)}
        </pre>
      </Card>

      <Card>
        <CardHeader title="Optimization policies" />
        <pre className="overflow-auto rounded-lg bg-black/5 p-3 text-xs">
          {JSON.stringify(policies, null, 2)}
        </pre>
      </Card>
    </div>
  );
}
