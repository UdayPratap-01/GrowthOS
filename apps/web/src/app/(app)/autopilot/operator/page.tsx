"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import {
  CanaryStatus,
  CapabilityItem,
  OperatorStatus,
  ProviderReadiness,
  humanStage,
  readinessTone,
} from "@/lib/operator";

const links = [
  { href: "/integrations", label: "Integrations (OAuth)" },
  { href: "/autopilot/recommendations", label: "Recommendations" },
  { href: "/autopilot/actions", label: "AI Actions" },
  { href: "/autopilot/reconciliation", label: "Reconciliation" },
  { href: "/autopilot/settings", label: "Safety settings" },
];

const READ_ONLY_CONFIRM = "I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION";
const CANARY_CONFIRM = "I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION";

type ProviderItem = {
  provider: string;
  status: string;
  credentials_configured: boolean;
  integration_connected: boolean;
  account_hint: string | null;
  demo_mode: boolean;
  safe_for_read: boolean;
  safe_for_mutation: boolean;
  last_verification?: Record<string, unknown> | null;
  capability_matrix?: CapabilityItem[];
  readiness?: ProviderReadiness;
  mutation?: string;
};

type CanaryHistoryItem = {
  action_id: string;
  action_type: string;
  provider: string | null;
  campaign_target: string | null;
  status: string;
  risk: string | null;
  created_at: string | null;
  reconciliation_state?: string | null;
};

function verificationAgeHours(checkedAt: string | null | undefined): string {
  if (!checkedAt) return "—";
  const t = Date.parse(checkedAt);
  if (Number.isNaN(t)) return "—";
  const hours = (Date.now() - t) / 3_600_000;
  return `${hours.toFixed(1)}h`;
}

function campaignLabel(c: { id?: string; name?: string; status?: string }): string {
  const id = c.id || "—";
  const name = c.name ? ` ${c.name}` : "";
  const st = c.status ? ` [${c.status}]` : "";
  return `${id}${name}${st}`;
}

export default function AutopilotOperatorHome() {
  const [status, setStatus] = useState<OperatorStatus | null>(null);
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [canary, setCanary] = useState<CanaryStatus | null>(null);
  const [history, setHistory] = useState<CanaryHistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dryResult, setDryResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [providerSel, setProviderSel] = useState("meta");
  const [actionSel, setActionSel] = useState("pause_campaign");
  const [campaignId, setCampaignId] = useState("");

  async function load() {
    setError(null);
    try {
      const [s, p, c, h] = await Promise.all([
        api<OperatorStatus>("/autopilot/operator/status"),
        api<{ items: ProviderItem[] }>("/autopilot/operator/providers"),
        api<CanaryStatus>("/autopilot/operator/canary/status"),
        api<{ items: CanaryHistoryItem[] }>("/autopilot/operator/canary/history").catch(() => ({
          items: [],
        })),
      ]);
      setStatus(s);
      setProviders(p.items || []);
      setCanary(c);
      setHistory(h.items || []);
      if (c.eligible_actions?.length) {
        setActionSel(c.eligible_actions[0]);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function verify(provider: string) {
    const ok = window.confirm(
      "This performs READ-ONLY provider verification.\n\nNo campaigns, ads, budgets, or spend will be changed.\nAutonomous execution will NOT be enabled."
    );
    if (!ok) return;
    setBusy(`verify-${provider}`);
    try {
      await api(`/autopilot/operator/providers/${provider}/verify`, {
        method: "POST",
        body: JSON.stringify({ confirm: READ_ONLY_CONFIRM }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function dryRun() {
    setBusy("dry-run");
    setDryResult(null);
    try {
      const body: Record<string, unknown> = {
        provider: providerSel,
        action_type: actionSel,
      };
      if (campaignId.trim()) body.campaign_id = campaignId.trim();
      const res = await api<{
        eligible: boolean;
        gate?: { blocked_code?: string; blocked_reason?: string; readiness?: string };
        proposed_action?: Record<string, unknown>;
      }>("/autopilot/operator/canary/dry-run", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setDryResult(
        res.eligible
          ? `Dry-run ALLOWED — readiness ${res.gate?.readiness || "READY"} (no mutation)`
          : `Dry-run BLOCKED — ${res.gate?.blocked_code || "BLOCKED"}: ${res.gate?.blocked_reason || ""}`
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function executeCanary() {
    const ok = window.confirm(
      "LIVE CANARY — this may mutate a real test campaign.\n\nOnly proceed if allowlists, verification, and dry-run are ready.\n\nType confirmation is enforced server-side."
    );
    if (!ok) return;
    setBusy("execute");
    try {
      const body: Record<string, unknown> = {
        provider: providerSel,
        action_type: actionSel,
        confirm: CANARY_CONFIRM,
      };
      if (campaignId.trim()) body.campaign_id = campaignId.trim();
      const res = await api<{
        executed?: boolean;
        blocked_code?: string;
        blocked_reason?: string;
        post_verification?: { outcome?: string };
        reconciliation_blocks_retry?: boolean;
      }>("/autopilot/operator/canary/execute", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (res.executed) {
        setDryResult(
          `Executed — post-verify ${res.post_verification?.outcome || "—"}${
            res.reconciliation_blocks_retry ? " — RECONCILIATION REQUIRED" : ""
          }`
        );
      } else {
        setDryResult(`Blocked — ${res.blocked_code}: ${res.blocked_reason || ""}`);
      }
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Skeleton className="h-80 w-full" />;
  if (error && !status) return <EmptyState title="Operator status unavailable" description={error} />;
  if (!status) return <EmptyState title="No status" description="Insufficient data." />;

  const killActive = status.autonomous_kill_switch;
  const canaryReady = canary?.readiness === "READY" && !killActive && Boolean(canary?.canary_enabled);
  const executeDisabled = !canaryReady || busy !== null || !campaignId.trim();
  const prod = status.production_readiness;
  const selectedProvider = providers.find((p) => p.provider === providerSel);
  const discoveredCampaigns = selectedProvider?.readiness?.campaigns || [];

  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">Operator control</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Monitor optimization, kill switches, and provider safety. Approvals always re-evaluate policy on the
          server.
        </p>
      </div>

      {prod ? (
        <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--panel-soft)] px-4 py-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge tone="success">{prod.banners.code}</Badge>
            <Badge tone={prod.real_provider_verification_pending ? "warning" : "success"}>
              {prod.banners.providers}
            </Badge>
            <Badge tone="warning">{prod.banners.deployment}</Badge>
            {prod.demo_mode ? <Badge tone="demo">DEMO MODE ON</Badge> : <Badge>LIVE PATH DEFAULTS</Badge>}
          </div>
          <p className="text-[var(--muted)]">
            Meta live verification: {prod.meta.live_verification} ({humanStage(prod.meta.stage)}). Google live
            verification: {prod.google.live_verification} ({humanStage(prod.google.stage)}). Autonomous latches
            remain OFF until controlled canaries pass — do not treat CONFIGURED as VERIFIED.
          </p>
        </div>
      ) : null}

      {killActive ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-700 dark:text-red-300">
          KILL SWITCH ACTIVE — NEW LIVE MUTATIONS BLOCKED
        </div>
      ) : null}

      {error ? <EmptyState title="Notice" description={error} /> : null}

      <div className="flex flex-wrap gap-2">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:bg-[var(--panel-soft)]"
          >
            {l.label}
          </Link>
        ))}
        <Link href="/autopilot" className="rounded-lg px-3 py-2 text-sm text-[var(--muted)] underline">
          Classic Autopilot
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader title="Optimization" />
          <Badge>{status.optimization_enabled ? "ENABLED" : "DISABLED"}</Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">Mode: {status.optimization_mode}</p>
        </Card>
        <Card>
          <CardHeader title="Kill switch" />
          <Badge tone={status.autonomous_kill_switch ? "danger" : "default"}>
            {status.autonomous_kill_switch ? "ON" : "OFF"}
          </Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">{status.kill_switch.effect}</p>
        </Card>
        <Card>
          <CardHeader title="Autonomous execution" />
          <Badge>{status.autonomous_execution_enabled ? "ENABLED" : "DISABLED"}</Badge>
          <p className="mt-2 text-sm text-[var(--muted)]">Org automation: {String(status.automation_enabled)}</p>
        </Card>
        <Card>
          <CardHeader title="Usage today" />
          <p className="text-2xl font-medium">
            {status.usage.closed_loop_actions_today}/{status.usage.max_actions_per_day}
          </p>
          <p className="mt-2 text-sm text-[var(--muted)]">Closed-loop actions</p>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="META / GOOGLE readiness"
          subtitle="Connect → verify (read-only) → canary. Never claim verified before a real provider check."
        />
        <div className="grid gap-3 md:grid-cols-2">
          {(["meta", "google_ads"] as const).map((key) => {
            const p = providers.find((x) => x.provider === key);
            const r =
              p?.readiness ||
              status.provider_readiness?.[key] ||
              ({ stage: "NOT_CONFIGURED" } as ProviderReadiness);
            const canaryRow = canary?.providers?.find((x) => x.provider === key || (key === "meta" && x.provider === "meta"));
            return (
              <div key={key} className="rounded-lg border border-[var(--line)] p-4 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium uppercase">{key === "google_ads" ? "GOOGLE" : "META"}</span>
                  <Badge tone={readinessTone(r.stage)}>{humanStage(r.stage)}</Badge>
                </div>
                <div className="mt-3 space-y-1 text-[var(--muted)]">
                  <p>Credentials: {r.credentials_configured || p?.credentials_configured ? "Configured" : "Missing"}</p>
                  <p>OAuth: {r.connected || p?.integration_connected ? "Connected" : "Not connected"}</p>
                  <p>Verification: {humanStage(r.verification_status || "NOT RUN")}</p>
                  <p>Last verification: {String(r.verification_checked_at || "—")}</p>
                  <p>Age: {verificationAgeHours(r.verification_checked_at || undefined)}</p>
                  <p>
                    Account:{" "}
                    {r.account?.name ||
                      r.account?.id ||
                      p?.account_hint ||
                      canaryRow?.account_hint ||
                      "—"}
                  </p>
                  <p>
                    Canary:{" "}
                    {canary?.canary_enabled
                      ? canary.readiness === "READY" && r.stage === "VERIFIED"
                        ? "Ready (org gates)"
                        : "Blocked / incomplete"
                      : "Disabled"}
                  </p>
                  {r.failure_reason ? (
                    <p className="text-rose-700 dark:text-rose-300">Failure: {r.failure_reason}</p>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Provider verification (read-only)"
          subtitle="Verified ≠ autonomous spend enabled. Refresh before live canary."
        />
        <div className="grid gap-3 md:grid-cols-2">
          {providers.map((p) => {
            const last = p.last_verification || {};
            const auth = (last.authentication as { status?: string } | undefined)?.status || "NOT_CHECKED";
            const caps = p.capability_matrix || (last.capabilities as CapabilityItem[]) || [];
            const resources = (last.canary_resources as { campaigns?: Array<{ id?: string; name?: string; status?: string }> } | undefined) || {};
            const campaigns = p.readiness?.campaigns?.length
              ? p.readiness.campaigns
              : Array.isArray(resources.campaigns)
                ? resources.campaigns
                : [];
            const stage = p.readiness?.stage || p.status;
            const failure =
              p.readiness?.failure_reason ||
              (last.error_category as string | undefined) ||
              (last.skipped_reason as string | undefined);
            return (
              <div key={p.provider} className="rounded-lg border border-[var(--line)] p-4 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium uppercase">{p.provider}</span>
                  <Badge tone={readinessTone(stage)}>{humanStage(stage)}</Badge>
                </div>
                <div className="mt-3 space-y-1 text-[var(--muted)]">
                  <p>Configuration: {p.credentials_configured ? "CONFIGURED" : "NOT_CONFIGURED"}</p>
                  <p>Authentication: {auth}</p>
                  <p>Account: {p.account_hint || (last.account as { name?: string } | undefined)?.name || "—"}</p>
                  <p>Read access: {p.safe_for_read || last.safe_for_read ? "VERIFIED/READY" : "NOT_CHECKED"}</p>
                  <p>
                    Connection:{" "}
                    {p.integration_connected
                      ? p.status === "VERIFIED" || stage === "VERIFIED"
                        ? "Verified"
                        : "Connected"
                      : p.credentials_configured
                        ? "Not connected"
                        : "Not configured"}
                  </p>
                  <p>Last verification: {String(last.checked_at || p.readiness?.verification_checked_at || "—")}</p>
                  <p>Age: {verificationAgeHours(String(last.checked_at || p.readiness?.verification_checked_at || ""))}</p>
                  {failure ? <p className="text-rose-700 dark:text-rose-300">Failure reason: {failure}</p> : null}
                  <div className="pt-2">
                    <p className="font-medium text-[var(--ink)]">Capabilities</p>
                    <ul className="mt-1 space-y-0.5">
                      {(caps.length ? caps : [{ operation: "—", status: "UNKNOWN" }]).map((c) => (
                        <li key={`${p.provider}-${c.operation}`}>
                          {c.operation}: <span className="uppercase">{c.status}</span>
                          {c.operation === "update_budget" && p.provider === "google_ads" ? " (deferred)" : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="pt-2">
                    <p className="font-medium text-[var(--ink)]">Campaigns discovered</p>
                    {campaigns.length ? (
                      <ul className="mt-1 max-h-28 overflow-y-auto space-y-0.5 font-mono text-xs">
                        {campaigns.slice(0, 12).map((c, idx) => (
                          <li key={`${c.id || idx}`}>{campaignLabel(c)}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>—</p>
                    )}
                  </div>
                </div>
                <Button
                  className="mt-3"
                  size="sm"
                  disabled={busy === `verify-${p.provider}`}
                  onClick={() => verify(p.provider === "google_ads" ? "google_ads" : "meta")}
                >
                  Refresh verification
                </Button>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Live Canary"
          subtitle="Controlled single-action execution via ActionService. Frontend disable is not security — server gates enforce all rules."
        />
        {canary ? (
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={canary.readiness === "READY" ? "success" : "danger"}>{canary.readiness}</Badge>
              <Badge>{canary.canary_enabled ? "CANARY ON" : "CANARY OFF"}</Badge>
              <span className="text-[var(--muted)]">env={canary.environment}</span>
            </div>
            {canary.notes?.length ? (
              <ul className="list-disc space-y-1 pl-5 text-[var(--muted)]">
                {canary.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
            {canary.providers?.length ? (
              <div className="grid gap-2 md:grid-cols-2">
                {canary.providers.map((cp) => (
                  <div key={cp.provider} className="rounded-md border border-[var(--line)] p-3 text-[var(--muted)]">
                    <p className="font-medium uppercase text-[var(--ink)]">{cp.provider}</p>
                    <p>Connected: {String(cp.connected)}</p>
                    <p>Verification: {cp.verification_status || "—"}</p>
                    <p>Checked: {cp.verification_checked_at || "—"}</p>
                    <p>Account: {cp.account_hint || "—"}</p>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="grid gap-2 md:grid-cols-2 text-[var(--muted)]">
              <p>Providers allowlist: {canary.allowlists.providers || "(empty)"}</p>
              <p>Actions allowlist: {canary.allowlists.actions || "(empty)"}</p>
              <p>
                Daily capacity: {canary.limits.actions_remaining_24h}/{canary.limits.max_actions_per_day} remaining
              </p>
              <p>Verification max age: {canary.limits.verification_max_age_hours}h</p>
              <p>Meta accounts configured: {String(canary.allowlists.meta_ad_accounts_configured)}</p>
              <p>Google customers configured: {String(canary.allowlists.google_customers_configured)}</p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <label className="block">
                <span className="text-xs text-[var(--muted)]">Provider</span>
                <select
                  className="mt-1 w-full rounded-md border border-[var(--border)] bg-transparent px-2 py-2"
                  value={providerSel}
                  onChange={(e) => setProviderSel(e.target.value)}
                >
                  <option value="meta">meta</option>
                  <option value="google_ads">google_ads</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-[var(--muted)]">Action</span>
                <select
                  className="mt-1 w-full rounded-md border border-[var(--border)] bg-transparent px-2 py-2"
                  value={actionSel}
                  onChange={(e) => setActionSel(e.target.value)}
                >
                  {(canary.eligible_actions.length
                    ? canary.eligible_actions
                    : canary.preferred_actions
                  ).map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-[var(--muted)]">GrowthOS campaign UUID</span>
                <input
                  className="mt-1 w-full rounded-md border border-[var(--border)] bg-transparent px-2 py-2"
                  value={campaignId}
                  onChange={(e) => setCampaignId(e.target.value)}
                  placeholder="required for dry-run/execute"
                />
              </label>
            </div>
            {discoveredCampaigns.length ? (
              <div className="text-xs text-[var(--muted)]">
                <p className="mb-1 font-medium text-[var(--ink)]">
                  Provider campaign ids (for allowlists — canary still needs GrowthOS UUID):
                </p>
                <ul className="max-h-24 overflow-y-auto font-mono">
                  {discoveredCampaigns.slice(0, 8).map((c, idx) => (
                    <li key={`${c.id || idx}`}>{campaignLabel(c)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy !== null} onClick={dryRun}>
                Dry Run
              </Button>
              <Button size="sm" disabled={executeDisabled} onClick={executeCanary}>
                Execute Canary
              </Button>
              <Link href="/autopilot/reconciliation" className="text-sm underline px-2 py-2">
                Reconciliation status
              </Link>
              <Link href="/autopilot/actions" className="text-sm underline px-2 py-2">
                View Result / Audit
              </Link>
            </div>
            {dryResult ? <p className="text-[var(--muted)]">{dryResult}</p> : null}
            <p className="text-xs text-[var(--muted)]">
              Confirm phrase (server-validated): {canary.confirm_phrase}. Provider verified ≠ autonomous spend.
              Canary success ≠ unrestricted autonomy. Google budget mutation is UNSUPPORTED.
            </p>
            {history.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[var(--muted)]">
                      <th className="py-1 pr-2">When</th>
                      <th className="py-1 pr-2">Provider</th>
                      <th className="py-1 pr-2">Action</th>
                      <th className="py-1 pr-2">Status</th>
                      <th className="py-1 pr-2">Recon</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.slice(0, 10).map((h) => (
                      <tr key={h.action_id} className="border-t border-[var(--line)]">
                        <td className="py-1 pr-2">{h.created_at || "—"}</td>
                        <td className="py-1 pr-2">{h.provider || "—"}</td>
                        <td className="py-1 pr-2">{h.action_type}</td>
                        <td className="py-1 pr-2">{h.status}</td>
                        <td className="py-1 pr-2">{h.reconciliation_state || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-[var(--muted)]">No canary execution history yet.</p>
            )}
          </div>
        ) : (
          <EmptyState title="Canary status unavailable" description="Could not load canary readiness." />
        )}
      </Card>
    </div>
  );
}
