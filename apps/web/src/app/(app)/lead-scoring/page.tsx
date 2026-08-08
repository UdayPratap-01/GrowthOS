"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { Client, LeadScoreSummary } from "@/types";

export default function LeadScoringPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [data, setData] = useState<LeadScoreSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setClients(await api<Client[]>("/clients"));
      setLoading(false);
    })();
  }, []);

  useEffect(() => {
    (async () => {
      setError(null);
      try {
        const qs = clientId ? `?client_id=${clientId}` : "";
        setData(await api<LeadScoreSummary>(`/lead-scoring${qs}`));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load lead scoring");
      }
    })();
  }, [clientId]);

  if (loading && !data) return <Skeleton className="h-64 w-full" />;
  if (error) return <EmptyState title="Lead scoring unavailable" description={error} />;
  if (!data) return null;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Lead Scoring</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">0–100 scores with explanations from available CRM fields only.</p>
        </div>
        <Select className="w-52" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">All clients</option>
          {clients.map((c) => <option key={c.id} value={c.id}>{c.business_name}</option>)}
        </Select>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card><div className="text-xs text-[var(--muted)]">Total leads</div><div className="text-2xl font-display">{data.total_leads}</div></Card>
        <Card><div className="text-xs text-[var(--muted)]">Average score</div><div className="text-2xl font-display">{data.average_score ?? "Insufficient data."}</div></Card>
        <Card><div className="text-xs text-[var(--muted)]">High intent (75+)</div><div className="text-2xl font-display">{data.high_intent}</div></Card>
        <Card><div className="text-xs text-[var(--muted)]">Medium / Low</div><div className="text-2xl font-display">{data.medium_intent} / {data.low_intent}</div></Card>
      </div>

      <Card>
        <CardHeader title="Scoring note" />
        <p className="text-sm text-[var(--muted)]">{data.data_note}</p>
      </Card>

      <Card>
        <CardHeader title="Top scored leads" />
        {!data.top_leads.length ? (
          <p className="text-sm text-[var(--muted)]">Insufficient data.</p>
        ) : (
          <div className="space-y-3">
            {data.top_leads.map((lead) => (
              <div key={lead.id} className="rounded-xl border border-[var(--line)] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="font-medium">{lead.name}</div>
                    <div className="text-xs text-[var(--muted)]">{lead.email || "—"} · {lead.source || "no source"}</div>
                  </div>
                  <Badge tone="accent">{lead.lead_score ?? "—"}/100</Badge>
                </div>
                <ul className="mt-2 list-disc pl-5 text-sm text-[var(--muted)]">
                  {(lead.score_explanation?.reasons || ["Insufficient data."]).map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
                {lead.score_explanation?.insufficient_data_note ? (
                  <p className="mt-2 text-xs text-[var(--muted)]">{lead.score_explanation.insufficient_data_note}</p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
