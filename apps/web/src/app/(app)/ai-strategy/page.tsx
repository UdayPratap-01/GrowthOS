"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Badge } from "@/components/ui/Badge";
import { api } from "@/lib/api";
import { Client, Strategy } from "@/types";

export default function AIStrategyPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const list = await api<Client[]>("/clients");
        setClients(list);
        if (list[0]) setClientId(list[0].id);
      } catch {
        // 401 handled by api() → login redirect
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!clientId) return;
    (async () => {
      try {
        const list = await api<Strategy[]>(`/clients/${clientId}/strategies`);
        setStrategy(list[0] || null);
      } catch {
        setStrategy(null);
      }
    })();
  }, [clientId]);

  async function generate() {
    if (!clientId) return;
    setBusy(true);
    try {
      const s = await api<Strategy>(`/clients/${clientId}/strategies/generate`, {
        method: "POST",
        body: JSON.stringify({ title: "Growth Plan" }),
      });
      setStrategy(s);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!clients.length) {
    return <EmptyState title="Add a client first" description="Strategy Engine needs a client context." actionLabel="Go to clients" onAction={() => (window.location.href = "/clients")} />;
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Strategy</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Situation → problems → opportunities → approved actions.</p>
        </div>
        <div className="flex gap-2">
          <Select className="w-56" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.business_name}</option>
            ))}
          </Select>
          <Button onClick={generate} disabled={busy}>{busy ? "Generating..." : "Generate strategy"}</Button>
        </div>
      </div>

      {!strategy ? (
        <EmptyState title="No strategy for this client" description="Generate one from available client + metric context." actionLabel="Generate" onAction={generate} />
      ) : (
        <>
          <Card>
            <CardHeader title={strategy.title} action={<Link href={`/clients/${clientId}?tab=strategy`}><Button size="sm" variant="secondary">Open workspace</Button></Link>} />
            <div className="grid gap-4 md:grid-cols-2 text-sm">
              <div><div className="text-[var(--muted)]">Current situation</div><p className="mt-1">{strategy.current_situation}</p></div>
              <div><div className="text-[var(--muted)]">What is happening?</div><p className="mt-1">{strategy.what_is_happening}</p></div>
              <div><div className="text-[var(--muted)]">Key problems</div><ul className="mt-1 list-disc pl-5">{strategy.key_problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
              <div><div className="text-[var(--muted)]">Opportunities</div><ul className="mt-1 list-disc pl-5">{strategy.opportunities.map((p) => <li key={p}>{p}</li>)}</ul></div>
            </div>
            <p className="mt-4 text-sm"><span className="font-medium">Strategy:</span> {strategy.strategy_summary}</p>
          </Card>
          <Card>
            <CardHeader title="Action plan" />
            <div className="space-y-3">
              {strategy.actions.map((a) => (
                <div key={a.id} className="rounded-xl border border-[var(--line)] p-4">
                  <div className="mb-2 flex gap-2"><Badge tone={a.priority}>{a.priority}</Badge><Badge>{a.status}</Badge></div>
                  <div className="font-medium">{a.action}</div>
                  <p className="mt-1 text-sm text-[var(--muted)]">{a.channel} · {a.objective} · {a.expected_outcome}</p>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
