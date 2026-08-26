"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusDot } from "@/components/ui/StatusDot";
import { api } from "@/lib/api";
import { Client, IntegrationStatus } from "@/types";

const PHASE3 = new Set(["meta", "instagram", "whatsapp", "google_analytics"]);
const PHASE4 = new Set(["google_ads", "youtube"]);

function IntegrationsInner() {
  const params = useSearchParams();
  const [items, setItems] = useState<IntegrationStatus[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const qs = clientId ? `?client_id=${clientId}` : "";
      setItems(await api<IntegrationStatus[]>(`/integrations${qs}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load integrations");
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
  }, [clientId]);

  useEffect(() => {
    const connected = params.get("connected");
    const err = params.get("error");
    const detail = params.get("detail");
    if (connected) setNotice(`${connected} connected successfully. Tokens are stored encrypted server-side.`);
    if (err) setError(`${err}${detail ? `: ${detail}` : ""}`);
  }, [params]);

  async function connect(provider: string) {
    setBusy(provider);
    setError(null);
    // Optimistic "connecting" until the OAuth redirect completes or fails.
    // Success is only confirmed after the callback returns connected.
    setItems((prev) =>
      prev.map((item) =>
        item.provider === provider
          ? { ...item, status: "connecting", message: "Redirecting to the provider…" }
          : item,
      ),
    );
    try {
      const qs = clientId ? `?client_id=${clientId}` : "";
      const result = await api<{ authorize_url: string }>(`/integrations/${provider}/connect${qs}`, {
        method: "POST",
      });
      window.location.href = result.authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connect failed");
      setBusy(null);
      await load();
    }
  }

  async function sync(provider: string) {
    setBusy(provider);
    setError(null);
    try {
      const qs = clientId ? `?client_id=${clientId}` : "";
      const result = await api<{ success: boolean; message: string; records_synced: number }>(
        `/integrations/${provider}/sync${qs}`,
        { method: "POST" }
      );
      setNotice(result.message + (result.records_synced ? ` (${result.records_synced} records)` : ""));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(provider: string) {
    setBusy(provider);
    try {
      const qs = clientId ? `?client_id=${clientId}` : "";
      await api(`/integrations/${provider}/disconnect${qs}`, { method: "POST" });
      setNotice(`${provider} disconnected.`);
      // Reflect disconnected immediately; the next load confirms server state.
      setItems((prev) =>
        prev.map((item) =>
          item.provider === provider
            ? {
                ...item,
                status: "disconnected",
                message: "Disconnected. Tokens have been cleared server-side.",
                last_synced_at: null,
              }
            : item,
        ),
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Disconnect failed");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Integrations</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Phase 3–4 adapters. Statuses are honest — Connected is never faked without OAuth.
          </p>
        </div>
        <Select className="w-56" value={clientId} onChange={(e) => setClientId(e.target.value)}>
          <option value="">Organization-level</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>{c.business_name}</option>
          ))}
        </Select>
      </div>

      {notice ? (
        <Card>
          <p className="text-sm text-[var(--accent-ink)]">{notice}</p>
        </Card>
      ) : null}
      {error ? <EmptyState title="Integration error" description={error} /> : null}

      <Card>
        <CardHeader
          title="Platform adapters"
          subtitle="Statuses: Not connected · Connecting · Connected · Sync error · Disconnected · Demo data"
        />
        <div className="space-y-3">
          {items.map((item) => {
            const phaseLabel = PHASE3.has(item.provider)
              ? "Phase 3"
              : PHASE4.has(item.provider)
                ? "Phase 4"
                : "Later";
            return (
              <div
                key={item.provider}
                className="flex flex-col gap-3 rounded-xl border border-[var(--line)] p-4 md:flex-row md:items-center md:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="font-medium capitalize">{item.provider.replaceAll("_", " ")}</div>
                    <Badge tone={PHASE4.has(item.provider) ? "accent" : "default"}>{phaseLabel}</Badge>
                    {item.credentials_configured ? <Badge tone="success">App credentials set</Badge> : null}
                  </div>
                  <p className="mt-1 text-sm text-[var(--muted)]">{item.message}</p>
                  {item.account_label ? (
                    <p className="mt-1 text-xs text-[var(--muted)]">Account: {item.account_label}</p>
                  ) : null}
                  {item.last_synced_at ? (
                    <p className="mt-1 text-xs text-[var(--muted)]">Last sync: {item.last_synced_at}</p>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusDot status={item.status} label={item.status.replaceAll("_", " ")} />
                  {item.can_connect &&
                  (item.status === "not_connected" ||
                    item.status === "disconnected" ||
                    item.status === "connecting") ? (
                    <Button
                      size="sm"
                      disabled={busy === item.provider || item.status === "connecting"}
                      onClick={() => connect(item.provider)}
                    >
                      {item.status === "connecting" || busy === item.provider ? "Connecting…" : "Connect"}
                    </Button>
                  ) : null}
                  {item.status === "connected" || item.status === "sync_error" ? (
                    <>
                      <Button size="sm" variant="secondary" disabled={busy === item.provider} onClick={() => sync(item.provider)}>
                        Sync
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy === item.provider} onClick={() => disconnect(item.provider)}>
                        Disconnect
                      </Button>
                    </>
                  ) : null}
                  {item.status === "demo_data" ? (
                    <Button size="sm" variant="secondary" disabled={busy === item.provider} onClick={() => sync(item.provider)}>
                      Try sync
                    </Button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <CardHeader title="Setup notes" />
        <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--muted)]">
          <li>Set META_APP_ID / META_APP_SECRET for Meta, Instagram, and WhatsApp OAuth.</li>
          <li>Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET for Google Analytics and YouTube.</li>
          <li>Google Ads also needs GOOGLE_ADS_DEVELOPER_TOKEN (optional GOOGLE_ADS_LOGIN_CUSTOMER_ID for MCC).</li>
          <li>Add redirect URIs for each provider callback under API_PUBLIC_URL.</li>
          <li>Access tokens are encrypted with ENCRYPTION_KEY and never returned to the browser.</li>
          <li>Demo Data means seeded analytics only — not a live platform connection.</li>
        </ul>
      </Card>
    </div>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <IntegrationsInner />
    </Suspense>
  );
}
