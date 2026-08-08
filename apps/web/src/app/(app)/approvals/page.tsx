"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { AIAction } from "@/types";

const PRIORITY_ORDER = ["critical", "high", "medium", "low"] as const;

export default function ApprovalsPage() {
  const [items, setItems] = useState<AIAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      setItems(await api<AIAction[]>("/autopilot/actions?status=PENDING"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const grouped = useMemo(() => {
    const map: Record<string, AIAction[]> = { critical: [], high: [], medium: [], low: [] };
    for (const item of items) {
      const key = (item.priority || "medium").toLowerCase();
      (map[key] || map.medium).push(item);
    }
    return map;
  }, [items]);

  async function decide(id: string, kind: "approve" | "reject") {
    setBusy(id);
    try {
      await api(`/autopilot/actions/${id}/${kind}`, {
        method: "POST",
        body: JSON.stringify({ note: kind === "approve" ? "Approved from Approval Center" : "Rejected from Approval Center" }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${kind} failed`);
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">Approval Center</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          High-impact AI actions wait here. Approve executes through the safety + execution engine.
        </p>
      </div>

      {error ? <EmptyState title="Approval error" description={error} /> : null}

      {items.length === 0 ? (
        <EmptyState title="No pending approvals" description="Run Autopilot or the AI Assistant to create structured actions." />
      ) : null}

      {PRIORITY_ORDER.map((level) => {
        const rows = grouped[level] || [];
        if (!rows.length) return null;
        return (
          <Card key={level}>
            <CardHeader
              title={`${level.replace("_", " ")} priority`}
              action={<Badge tone={level === "high" || level === "critical" ? "danger" : "accent"}>{rows.length}</Badge>}
            />
            <div className="space-y-3">
              {rows.map((a) => (
                <div key={a.id} className="rounded-xl border border-[var(--line)] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-medium">{a.description}</div>
                      <p className="mt-1 text-sm text-[var(--muted)]">{a.reason}</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                        <span>{a.action_type}</span>
                        <span>· {a.agent}</span>
                        <span>· {a.platform || "n/a"}</span>
                        <span>· risk {a.risk_level}</span>
                        {a.estimated_cost != null ? <span>· est. {formatCurrency(a.estimated_cost)}</span> : null}
                        {a.demo_mode ? <Badge tone="demo">Demo</Badge> : null}
                      </div>
                      {a.evidence?.length ? (
                        <ul className="mt-2 list-disc pl-5 text-xs text-[var(--muted)]">
                          {a.evidence.slice(0, 4).map((e, idx) => (
                            <li key={idx}>{typeof e === "string" ? e : JSON.stringify(e)}</li>
                          ))}
                        </ul>
                      ) : null}
                      {a.expected_impact ? (
                        <p className="mt-2 text-xs text-[var(--muted)]">Expected impact: {a.expected_impact}</p>
                      ) : null}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" disabled={busy === a.id} onClick={() => decide(a.id, "approve")}>
                        Approve
                      </Button>
                      <Button size="sm" variant="danger" disabled={busy === a.id} onClick={() => decide(a.id, "reject")}>
                        Reject
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
