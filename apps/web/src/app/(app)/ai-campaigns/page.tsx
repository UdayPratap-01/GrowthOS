"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import {
  REVIEW_STATUSES,
  type CampaignGenerationRun,
  type GeneratedCampaign,
} from "@/types/campaign-generation";
import {
  fetchGeneratedCampaigns,
  fetchRuns,
  reviewStatusLabel,
  reviewStatusTone,
  stageProgressLabel,
} from "@/lib/campaign-generation";
import type { Client } from "@/types";

/**
 * AI-generated campaigns and the runs that produced them.
 *
 * Runs that are still working are listed separately and refreshed on an
 * interval, because a run started here can outlive the tab that started it —
 * the work happens in a background worker, not in the browser.
 */
export default function AiCampaignsPage() {
  const [campaigns, setCampaigns] = useState<GeneratedCampaign[]>([]);
  const [runs, setRuns] = useState<CampaignGenerationRun[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [reviewStatus, setReviewStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [campaignList, runList, clientList] = await Promise.all([
        fetchGeneratedCampaigns({
          clientId: clientId || undefined,
          reviewStatus: reviewStatus || undefined,
        }),
        fetchRuns({ clientId: clientId || undefined, limit: 20 }),
        api<Client[]>("/clients"),
      ]);
      setCampaigns(campaignList);
      setRuns(runList);
      setClients(clientList);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, reviewStatus]);

  const active = useMemo(() => runs.filter((run) => !run.terminal), [runs]);

  // Only poll while something is actually in flight.
  useEffect(() => {
    if (active.length === 0) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active.length, clientId, reviewStatus]);

  const clientName = useMemo(() => {
    const map = Object.fromEntries(clients.map((c) => [c.id, c.business_name]));
    return (id: string) => map[id] || id.slice(0, 8);
  }, [clients]);

  if (loading) return <Skeleton className="h-80 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Campaigns</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Generated campaign packages awaiting human review. None of these have been published.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/ai-campaigns/new">
            <Button>Create campaign with AI</Button>
          </Link>
          <Link href="/creative-library">
            <Button variant="secondary">Creative Library</Button>
          </Link>
        </div>
      </div>

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      {active.length ? (
        <Card>
          <CardHeader
            title={`In progress (${active.length})`}
            subtitle="Live status from the background worker."
            action={
              <Button size="sm" variant="ghost" onClick={load}>
                Refresh
              </Button>
            }
          />
          <div className="space-y-3">
            {active.map((run) => (
              <div key={run.id} className="rounded-xl border border-[var(--line)] p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Badge tone="accent">{run.status.replaceAll("_", " ")}</Badge>
                  <span className="text-sm font-medium">{clientName(run.client_id)}</span>
                  <span className="text-xs text-[var(--muted)]">
                    {run.platform} · {run.objective.replaceAll("_", " ")}
                  </span>
                  {run.campaign_id ? (
                    <Link
                      href={`/ai-campaigns/${run.campaign_id}`}
                      className="ml-auto text-sm text-[var(--accent-ink)] underline"
                    >
                      Open
                    </Link>
                  ) : null}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                  {run.stages.map((stage) => {
                    const progress = stageProgressLabel(stage);
                    return (
                      <span key={stage.key}>
                        {stage.label}
                        {progress ? ` ${progress}` : ""}
                        {stage.status.toUpperCase() === "COMPLETED" ? " ✓" : ""}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="flex flex-wrap gap-3">
          <Select className="w-52" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.business_name}
              </option>
            ))}
          </Select>
          <Select
            className="w-52"
            value={reviewStatus}
            onChange={(e) => setReviewStatus(e.target.value)}
          >
            <option value="">All statuses</option>
            {REVIEW_STATUSES.map((status) => (
              <option key={status} value={status}>
                {reviewStatusLabel(status)}
              </option>
            ))}
          </Select>
          <Button variant="secondary" onClick={load}>
            Refresh
          </Button>
        </div>
      </Card>

      {campaigns.length === 0 ? (
        <EmptyState
          title="No AI campaigns yet"
          description="Generate a campaign package from a client's stored context. Strategy, copy, creative concepts and media are produced for review — nothing is published."
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {campaigns.map((campaign) => (
            <Link key={campaign.id} href={`/ai-campaigns/${campaign.id}`}>
              <Card className="h-full transition hover:border-[var(--accent)]">
                <div className="flex items-start justify-between gap-2">
                  <div className="font-medium">{campaign.name}</div>
                  <Badge tone={reviewStatusTone(campaign.review_status)}>
                    {reviewStatusLabel(campaign.review_status)}
                  </Badge>
                </div>
                <div className="mt-2 text-xs text-[var(--muted)]">
                  {clientName(campaign.client_id)} · {campaign.platform}
                  {campaign.objective ? ` · ${campaign.objective.replaceAll("_", " ")}` : ""}
                </div>
                <div className="mt-3 flex flex-wrap gap-3 text-sm">
                  {campaign.daily_budget ? (
                    <span>
                      <span className="text-[var(--muted)]">Daily </span>
                      {campaign.currency} {campaign.daily_budget}
                    </span>
                  ) : null}
                  {campaign.total_budget ? (
                    <span>
                      <span className="text-[var(--muted)]">Total </span>
                      {campaign.currency} {campaign.total_budget}
                    </span>
                  ) : null}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {campaign.data_source === "demo" ? <Badge tone="demo">Demo</Badge> : null}
                  {campaign.generated_by_ai ? <Badge tone="accent">AI generated</Badge> : null}
                  <span className="text-xs text-[var(--muted)]">
                    {new Date(campaign.created_at).toLocaleDateString()}
                  </span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
