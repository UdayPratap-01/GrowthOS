"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import { PerformanceRec } from "@/lib/operator";

type ListOut = { items: PerformanceRec[]; total: number };

export default function AutopilotRecommendationsPage() {
  const [items, setItems] = useState<PerformanceRec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setError(null);
    try {
      const data = await api<ListOut>("/analytics/recommendations?limit=50");
      setItems(data.items || []);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function act(id: string, kind: "approve" | "reject") {
    setBusy(id);
    try {
      await api(`/analytics/recommendations/${id}/${kind}`, { method: "POST" });
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
          <h1 className="font-display text-3xl">Recommendations</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Approve runs backend policy re-evaluation. Frontend never bypasses safety.
          </p>
        </div>
        <Link href="/autopilot/operator" className="text-sm underline text-[var(--muted)]">
          Operator home
        </Link>
      </div>
      {error ? <EmptyState title="Error" description={error} /> : null}
      {items.length === 0 ? (
        <EmptyState title="No recommendations" description="Run analytics analyze or an autopilot cycle." />
      ) : null}
      <div className="space-y-3">
        {items.map((rec) => (
          <Card key={rec.id}>
            <CardHeader
              title={rec.title}
              action={<Badge>{rec.status}</Badge>}
            />
            <div className="grid gap-2 text-sm md:grid-cols-2">
              <p>Platform: {rec.platform}</p>
              <p>Campaign: {rec.external_campaign_id || "—"}</p>
              <p>Signal: {rec.signal_category || rec.recommendation_type}</p>
              <p>Confidence: {String(rec.confidence)}</p>
              <p>Created: {new Date(rec.created_at).toLocaleString()}</p>
              <p>Expires: {rec.expires_at ? new Date(rec.expires_at).toLocaleString() : "—"}</p>
            </div>
            <p className="mt-3 text-sm text-[var(--muted)]">{rec.explanation}</p>
            <pre className="mt-3 overflow-auto rounded-lg bg-black/5 p-3 text-xs">
              {JSON.stringify(rec.suggested_action || {}, null, 2)}
            </pre>
            <div className="mt-3 flex gap-2">
              <Button disabled={busy === rec.id} onClick={() => act(rec.id, "approve")}>
                Approve
              </Button>
              <Button variant="secondary" disabled={busy === rec.id} onClick={() => act(rec.id, "reject")}>
                Reject
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
