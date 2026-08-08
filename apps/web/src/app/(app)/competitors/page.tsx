"use client";

import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { api } from "@/lib/api";
import { Client, Competitor } from "@/types";

export default function CompetitorsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [items, setItems] = useState<Competitor[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", url: "", notes: "", observations: "" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const list = await api<Client[]>("/clients");
      setClients(list);
      if (list[0]) setClientId(list[0].id);
      setLoading(false);
    })();
  }, []);

  async function load(id: string) {
    setError(null);
    try {
      setItems(await api<Competitor[]>(`/clients/${id}/competitors`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load competitors");
    }
  }

  useEffect(() => {
    if (clientId) load(clientId);
  }, [clientId]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!clientId) return;
    await api(`/clients/${clientId}/competitors`, {
      method: "POST",
      body: JSON.stringify({
        name: form.name,
        url: form.url || null,
        notes: form.notes || null,
        observations: form.observations
          ? { summary: form.observations }
          : { note: "No performance claims without evidence." },
      }),
    });
    setForm({ name: "", url: "", notes: "", observations: "" });
    await load(clientId);
  }

  async function remove(id: string) {
    await api(`/clients/${clientId}/competitors/${id}`, { method: "DELETE" });
    await load(clientId);
  }

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!clients.length) return <EmptyState title="No clients" description="Competitors are tracked per client workspace." />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Competitors</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Observations only — no invented competitive metrics.
          </p>
        </div>
        <Select className="w-56" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          {clients.map((c) => <option key={c.id} value={c.id}>{c.business_name}</option>)}
        </Select>
      </div>

      {error ? <EmptyState title="Could not load competitors" description={error} /> : null}

      <Card>
        <CardHeader title="Add competitor" />
        <form className="grid gap-3 md:grid-cols-2" onSubmit={onCreate}>
          <Input required placeholder="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          <Input placeholder="URL" value={form.url} onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))} />
          <Textarea className="md:col-span-2" placeholder="Notes" value={form.notes} onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} />
          <Textarea className="md:col-span-2" placeholder="Observations (qualitative only)" value={form.observations} onChange={(e) => setForm((f) => ({ ...f, observations: e.target.value }))} />
          <Button type="submit">Save competitor</Button>
        </form>
      </Card>

      {items.length === 0 ? (
        <EmptyState title="No competitors tracked" description="Add qualitative observations for this client." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {items.map((item) => (
            <Card key={item.id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-display text-xl">{item.name}</div>
                  {item.url ? <a className="text-sm text-[var(--accent-ink)]" href={item.url} target="_blank" rel="noreferrer">{item.url}</a> : null}
                </div>
                <Button size="sm" variant="ghost" onClick={() => remove(item.id)}>Delete</Button>
              </div>
              <p className="mt-3 text-sm text-[var(--muted)]">{item.notes || "No notes"}</p>
              <pre className="mt-3 overflow-x-auto rounded-xl bg-[var(--surface-2)] p-3 text-xs">
                {JSON.stringify(item.observations || {}, null, 2)}
              </pre>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
