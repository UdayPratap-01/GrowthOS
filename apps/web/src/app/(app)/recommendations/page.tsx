"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { Client, Recommendation } from "@/types";

const STATUSES = ["pending", "approved", "rejected", "saved", "completed"] as const;

export default function RecommendationsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [items, setItems] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setClients(await api<Client[]>("/clients"));
      setLoading(false);
    })();
  }, []);

  async function load() {
    setError(null);
    const qs = clientId ? `?client_id=${clientId}` : "";
    try {
      setItems(await api<Recommendation[]>(`/recommendations${qs}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load recommendations");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

  async function generate() {
    setBusy(true);
    try {
      await api("/recommendations/generate", {
        method: "POST",
        body: JSON.stringify({ client_id: clientId || null }),
      });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(id: string, status: string) {
    await api(`/recommendations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    await load();
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Recommendations</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Evidence-backed actions with approve / reject / save / complete workflow.
          </p>
        </div>
        <div className="flex gap-2">
          <Select className="w-52" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.business_name}</option>)}
          </Select>
          <Button onClick={generate} disabled={busy}>{busy ? "Generating..." : "Generate from analytics"}</Button>
        </div>
      </div>

      {error ? <EmptyState title="Recommendations unavailable" description={error} /> : null}

      {!items.length ? (
        <EmptyState
          title="No recommendations"
          description="Generate recommendations from available analytics. Never invents metrics."
          actionLabel="Generate"
          onAction={generate}
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <Card key={item.id}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Badge tone={item.priority}>{item.priority}</Badge>
                <Badge>{item.status}</Badge>
                {item.client_name ? <span className="text-xs text-[var(--muted)]">{item.client_name}</span> : null}
              </div>
              <h3 className="font-medium text-lg">{item.title}</h3>
              <div className="mt-3 grid gap-3 text-sm md:grid-cols-2">
                <div><div className="text-[var(--muted)]">Problem</div><p>{item.problem}</p></div>
                <div><div className="text-[var(--muted)]">Evidence</div><p>{item.evidence}</p></div>
                <div><div className="text-[var(--muted)]">Recommendation</div><p>{item.recommendation}</p></div>
                <div><div className="text-[var(--muted)]">Expected impact</div><p>{item.expected_impact}</p></div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {STATUSES.map((s) => (
                  <Button key={s} size="sm" variant="secondary" onClick={() => setStatus(item.id, s)}>
                    {s}
                  </Button>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
