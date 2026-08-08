"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { AutopilotRun, AutopilotSummary, Client, AIAction } from "@/types";

export default function AutopilotPage() {
  const [summary, setSummary] = useState<AutopilotSummary | null>(null);
  const [actions, setActions] = useState<AIAction[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [goal, setGoal] = useState("Generate Leads");
  const [budget, setBudget] = useState("500");
  const [duration, setDuration] = useState("30");
  const [mode, setMode] = useState("assisted");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [run, setRun] = useState<AutopilotRun | null>(null);

  async function load() {
    setError(null);
    try {
      const [s, a, c] = await Promise.all([
        api<AutopilotSummary>("/autopilot/summary"),
        api<AIAction[]>("/autopilot/activity"),
        api<Client[]>("/clients"),
      ]);
      setSummary(s);
      setActions(a.slice(0, 12));
      setClients(c);
      if (!clientId && c[0]) setClientId(c[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Autopilot");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runLoop() {
    if (!clientId) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await api<{ message: string; actions_created: number }>("/autopilot/decision-loop", {
        method: "POST",
        body: JSON.stringify({ client_id: clientId, max_actions: 5, max_iterations: 1 }),
      });
      setNotice(result.message);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision loop failed");
    } finally {
      setBusy(false);
    }
  }

  async function startAutopilot() {
    if (!clientId) return;
    setBusy(true);
    setNotice(null);
    setRun(null);
    try {
      const result = await api<AutopilotRun>("/autopilot/run", {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          goal,
          budget: Number(budget),
          duration_days: Number(duration),
          platforms: ["meta", "instagram"],
          autonomy_mode: mode,
        }),
      });
      setRun(result);
      setNotice(`Autopilot run ${result.status}. Publishing stays blocked until approval + connected platforms.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Autopilot run failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error && !summary) return <EmptyState title="Autopilot unavailable" description={error} />;
  if (!summary) return <EmptyState title="No Autopilot data" description="Insufficient data." />;

  const tiles = [
    { label: "Pending approvals", value: summary.pending_approvals, href: "/approvals" },
    { label: "Executing", value: summary.executing, href: "/ai-activity" },
    { label: "Completed today", value: summary.completed_today, href: "/ai-activity" },
    { label: "Failed today", value: summary.failed_today, href: "/ai-activity" },
    { label: "Scheduled posts", value: summary.scheduled_posts, href: "/ai-activity" },
    { label: "Creatives", value: summary.creatives_generated, href: "/creative-library" },
    { label: "Open optimizations", value: summary.optimizations_open, href: "/ai-activity" },
    { label: "Campaigns monitored", value: summary.campaigns_monitored, href: "/campaigns" },
  ];

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Autopilot</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Controlled AI execution — structured actions, approvals, and safety limits. Never invents live success.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/campaign-builder"><Button size="sm" variant="secondary">Campaign Builder</Button></Link>
          <Link href="/creative-library"><Button size="sm" variant="ghost">Creative Library</Button></Link>
          <Badge tone={summary.automation_enabled ? "success" : "warning"}>
            {summary.automation_enabled ? "Automation on" : "Automation off"}
          </Badge>
          <Badge tone="accent">{summary.autonomy_mode}</Badge>
          {summary.demo_mode ? <Badge tone="demo">Demo mode</Badge> : null}
        </div>
      </div>

      {notice ? (
        <Card>
          <p className="text-sm text-[var(--accent-ink)]">{notice}</p>
        </Card>
      ) : null}
      {error ? <EmptyState title="Autopilot notice" description={error} /> : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {tiles.map((t) => (
          <Link key={t.label} href={t.href}>
            <Card className="transition hover:border-[var(--accent)]">
              <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{t.label}</div>
              <div className="mt-2 font-display text-3xl">{t.value}</div>
            </Card>
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader
          title="RUN MARKETING AUTOPILOT"
          subtitle="One-click workflow with live step progress. High-impact steps wait for approval; integrations never faked."
        />
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Client</span>
            <Select className="mt-1 w-full" value={clientId} onChange={(e) => setClientId(e.target.value)}>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>{c.business_name}</option>
              ))}
            </Select>
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Goal</span>
            <Input className="mt-1" value={goal} onChange={(e) => setGoal(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Budget</span>
            <Input className="mt-1" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Duration (days)</span>
            <Input className="mt-1" value={duration} onChange={(e) => setDuration(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Autonomy mode</span>
            <Select className="mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="copilot">Copilot</option>
              <option value="assisted">Assisted Autopilot</option>
              <option value="autonomous">Autonomous</option>
            </Select>
          </label>
        </div>
        <div className="mt-4">
          <Button disabled={busy || !clientId} onClick={startAutopilot}>
            {busy ? "Starting…" : "START AUTOPILOT"}
          </Button>
        </div>
        {run ? (
          <ol className="mt-6 space-y-2">
            {(run.steps || []).map((s) => (
              <li key={s.key} className="flex items-start gap-3 rounded-xl border border-[var(--line)] px-3 py-2 text-sm">
                <span className="mt-0.5 w-5 shrink-0">
                  {s.status === "completed" ? "✓" : s.status === "blocked" ? "!" : s.status === "running" ? "⏳" : "○"}
                </span>
                <div>
                  <div className="font-medium">{s.label}</div>
                  {s.detail ? <div className="text-[var(--muted)]">{s.detail}</div> : null}
                </div>
              </li>
            ))}
            {run.demo_mode ? <Badge tone="demo">DEMO DATA</Badge> : null}
          </ol>
        ) : null}
      </Card>

      <Card>
        <CardHeader
          title="Run controlled cycle"
          subtitle="Collect → analyze → propose structured actions. Execution still requires approvals in Copilot."
          action={
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !clientId} onClick={runLoop}>
                {busy ? "Running..." : "Run decision loop"}
              </Button>
              <Link href="/settings"><Button variant="ghost">Autonomy settings</Button></Link>
            </div>
          }
        />
        <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--muted)]">
          <li>Copilot: recommendations and pending actions only.</li>
          <li>Assisted: low-risk auto; high-impact needs approval.</li>
          <li>Autonomous: executes only within budget/platform/action limits.</li>
          <li>Live publish/ads writes require connected integrations — never faked.</li>
        </ul>
      </Card>

      <Card>
        <CardHeader
          title="Recent AI actions"
          action={<Link href="/ai-activity"><Button size="sm" variant="secondary">Open Activity</Button></Link>}
        />
        {actions.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No actions yet. Start Autopilot or run a decision loop.</p>
        ) : (
          <div className="space-y-2">
            {actions.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--line)] px-3 py-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium">{a.description}</div>
                  <div className="text-xs text-[var(--muted)]">
                    {a.agent} · {a.action_type} · {a.platform || "n/a"}
                    {a.demo_mode ? " · DEMO DATA" : ""}
                  </div>
                </div>
                <Badge tone={a.status === "FAILED" ? "danger" : a.status === "COMPLETED" ? "success" : "accent"}>
                  {a.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
