"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { Client } from "@/types";

const emptyForm = {
  business_name: "",
  industry: "",
  website: "",
  description: "",
  location: "",
  target_audience: "",
  products_services: "",
  marketing_goals: "",
  monthly_budget: "",
  brand_voice: "",
  competitors: "",
  primary_channels: "",
  kpis: "",
};

export default function ClientsPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [q, setQ] = useState("");
  const [industry, setIndustry] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (industry) params.set("industry", industry);
      const data = await api<Client[]>(`/clients?${params.toString()}`);
      setClients(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load clients");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api<Client>("/clients", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          monthly_budget: form.monthly_budget ? Number(form.monthly_budget) : null,
          competitors: form.competitors.split(",").map((s) => s.trim()).filter(Boolean),
          primary_channels: form.primary_channels.split(",").map((s) => s.trim()).filter(Boolean),
          kpis: form.kpis.split(",").map((s) => s.trim()).filter(Boolean),
        }),
      });
      setForm(emptyForm);
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setSaving(false);
    }
  }

  async function archive(id: string) {
    await api(`/clients/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Clients</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Isolated workspaces for each business you manage.</p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>{showForm ? "Close" : "Create client"}</Button>
      </div>

      <Card>
        <div className="grid gap-3 md:grid-cols-3">
          <Input placeholder="Search clients..." value={q} onChange={(e) => setQ(e.target.value)} />
          <Input placeholder="Filter industry..." value={industry} onChange={(e) => setIndustry(e.target.value)} />
          <Button variant="secondary" onClick={load}>
            Apply filters
          </Button>
        </div>
      </Card>

      {showForm ? (
        <Card>
          <CardHeader title="New client" subtitle="All fields feed client-aware AI agents." />
          <form className="grid gap-3 md:grid-cols-2" onSubmit={onCreate}>
            {Object.entries({
              business_name: "Business name",
              industry: "Industry",
              website: "Website",
              location: "Location",
              monthly_budget: "Monthly budget",
              brand_voice: "Brand voice",
            }).map(([key, label]) => (
              <div key={key}>
                <label className="mb-1 block text-xs uppercase tracking-wide text-[var(--muted)]">{label}</label>
                <Input
                  required={key === "business_name"}
                  value={form[key as keyof typeof form]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                />
              </div>
            ))}
            {[
              ["description", "Description"],
              ["target_audience", "Target audience"],
              ["products_services", "Products / services"],
              ["marketing_goals", "Marketing goals"],
              ["competitors", "Competitors (comma-separated)"],
              ["primary_channels", "Primary channels (comma-separated)"],
              ["kpis", "KPIs (comma-separated)"],
            ].map(([key, label]) => (
              <div key={key} className="md:col-span-2">
                <label className="mb-1 block text-xs uppercase tracking-wide text-[var(--muted)]">{label}</label>
                <Textarea
                  value={form[key as keyof typeof form]}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                />
              </div>
            ))}
            <div className="md:col-span-2">
              <Button type="submit" disabled={saving}>
                {saving ? "Saving..." : "Save client"}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : error ? (
        <EmptyState title="Could not load clients" description={error} actionLabel="Retry" onAction={load} />
      ) : clients.length === 0 ? (
        <EmptyState
          title="No clients yet"
          description="Create your first client workspace to unlock strategy, content, and CRM."
          actionLabel="Create client"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {clients.map((client) => (
            <Card key={client.id}>
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <Link href={`/clients/${client.id}`} className="font-display text-xl hover:text-[var(--accent-ink)]">
                    {client.business_name}
                  </Link>
                  <div className="mt-1 text-sm text-[var(--muted)]">{client.industry || "—"} · {client.location || "—"}</div>
                </div>
                <Badge>{client.status}</Badge>
              </div>
              <p className="line-clamp-2 text-sm text-[var(--muted)]">{client.description || "No description"}</p>
              <div className="mt-4 flex items-center justify-between text-sm">
                <span>Budget {client.monthly_budget ? formatCurrency(client.monthly_budget) : "—"}</span>
                <div className="flex gap-2">
                  <Link href={`/clients/${client.id}`}>
                    <Button size="sm">Open</Button>
                  </Link>
                  <Button size="sm" variant="ghost" onClick={() => archive(client.id)}>
                    Archive
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
