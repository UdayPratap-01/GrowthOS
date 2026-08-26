"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { normalizeActionLifecycle } from "@/lib/jobs";
import { AIAction, Client } from "@/types";

export default function AIActivityPage() {
  const [items, setItems] = useState<AIAction[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams();
      if (clientId) qs.set("client_id", clientId);
      if (status) qs.set("status", status);
      const suffix = qs.toString() ? `?${qs}` : "";
      setItems(await api<AIAction[]>(`/autopilot/actions${suffix}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    (async () => setClients(await api<Client[]>("/clients")))();
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, status]);

  async function retry(id: string) {
    setBusy(id);
    try {
      await api(`/autopilot/actions/${id}/execute`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setBusy(null);
    }
  }

  async function rollback(id: string) {
    setBusy(id);
    try {
      const result = await api<{ message: string }>(`/autopilot/actions/${id}/rollback`, { method: "POST" });
      setError(null);
      alert(result.message || "Rollback result recorded");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Activity</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Timeline of structured actions across agents and platforms.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select className="w-48" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.business_name}</option>
            ))}
          </Select>
          <Select className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["PENDING", "APPROVED", "EXECUTING", "COMPLETED", "FAILED", "REJECTED", "SCHEDULED", "CANCELLED"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>
        </div>
      </div>

      {error ? <EmptyState title="Activity error" description={error} /> : null}
      {loading ? <Skeleton className="h-64 w-full" /> : null}

      {!loading && items.length === 0 ? (
        <EmptyState title="No activity yet" description="Actions appear after Autopilot cycles, approvals, or assistant commands." />
      ) : null}

      {!loading && items.length > 0 ? (
        <Card>
          <CardHeader title="Action log" subtitle="DEMO DATA labels mean simulated — not live platform confirmation." />
          <div className="space-y-3">
            {items.map((a) => (
              <div key={a.id} className="rounded-xl border border-[var(--line)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{a.description}</div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      {a.agent} · {a.action_type} · {a.platform || "n/a"} · {new Date(a.created_at).toLocaleString()}
                    </div>
                    {a.error ? <p className="mt-2 text-xs text-rose-700">{a.error}</p> : null}
                    {a.result && Object.keys(a.result).length ? (
                      <p className="mt-2 text-xs text-[var(--muted)]">
                        Result:{" "}
                        {String(
                          (typeof a.result.message === "string" && a.result.message) ||
                            (typeof a.result.note === "string" && a.result.note) ||
                            JSON.stringify(a.result).slice(0, 160)
                        )}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      tone={
                        normalizeActionLifecycle(a.status) === "failed"
                          ? "danger"
                          : normalizeActionLifecycle(a.status) === "published"
                            ? "success"
                            : normalizeActionLifecycle(a.status) === "executing"
                              ? "accent"
                              : normalizeActionLifecycle(a.status) === "pending_approval"
                                ? "warning"
                                : "default"
                      }
                    >
                      {normalizeActionLifecycle(a.status).replaceAll("_", " ")}
                    </Badge>
                    <Badge tone="default">{a.status}</Badge>
                    {a.demo_mode ? <Badge tone="demo">Demo</Badge> : null}
                    {a.status === "FAILED" ? (
                      <Button size="sm" variant="secondary" disabled={busy === a.id} onClick={() => retry(a.id)}>
                        Retry
                      </Button>
                    ) : null}
                    {a.status === "COMPLETED" ? (
                      <Button size="sm" variant="ghost" disabled={busy === a.id} onClick={() => rollback(a.id)}>
                        Rollback
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
