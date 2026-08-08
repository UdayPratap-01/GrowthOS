"use client";

import Link from "next/link";
import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { StatusDot } from "@/components/ui/StatusDot";
import { api } from "@/lib/api";
import {
  CalendarItem,
  Campaign,
  Client,
  ContentGenerated,
  IntegrationStatus,
  LEAD_STAGES,
  Lead,
  SocialPost,
  Strategy,
} from "@/types";
import { formatCurrency, formatNumber, formatPercent } from "@/lib/utils";

const tabs = [
  "overview",
  "performance",
  "content",
  "campaigns",
  "leads",
  "strategy",
  "reports",
  "competitors",
  "integrations",
  "assistant",
] as const;

type Tab = (typeof tabs)[number];

function ClientWorkspace() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const clientId = params.id;
  const initialTab = (search.get("tab") as Tab) || "overview";

  const [tab, setTab] = useState<Tab>(initialTab);
  const [client, setClient] = useState<Client | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [posts, setPosts] = useState<SocialPost[]>([]);
  const [calendar, setCalendar] = useState<CalendarItem[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationStatus[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [assistantQ, setAssistantQ] = useState("What should I post this week?");
  const [assistantA, setAssistantA] = useState<string | null>(null);
  const [contentForm, setContentForm] = useState({
    platform: "Instagram",
    content_type: "Reel",
    objective: "Lead generation",
    audience: "",
    tone: "",
    topic: "Customer success story",
    cta: "Book a consult",
  });
  const [generated, setGenerated] = useState<ContentGenerated | null>(null);
  const [leadForm, setLeadForm] = useState({
    name: "",
    email: "",
    phone: "",
    source: "Website Form",
    campaign: "",
    status: "new",
  });
  const [leadView, setLeadView] = useState<"kanban" | "table">("kanban");

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [c, s, p, cal, l, integ, camps] = await Promise.all([
        api<Client>(`/clients/${clientId}`),
        api<Strategy[]>(`/clients/${clientId}/strategies`),
        api<SocialPost[]>(`/clients/${clientId}/content/posts`),
        api<CalendarItem[]>(`/clients/${clientId}/content/calendar`),
        api<Lead[]>(`/clients/${clientId}/leads`),
        api<IntegrationStatus[]>(`/integrations?client_id=${clientId}`),
        api<Campaign[]>(`/clients/${clientId}/campaigns`),
      ]);
      setClient(c);
      setStrategies(s);
      setPosts(p);
      setCalendar(cal);
      setLeads(l);
      setIntegrations(integ);
      setCampaigns(camps);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load workspace");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

  const latestStrategy = useMemo(() => strategies[0] || null, [strategies]);

  async function generateStrategy() {
    setBusy(true);
    try {
      const s = await api<Strategy>(`/clients/${clientId}/strategies/generate`, {
        method: "POST",
        body: JSON.stringify({ title: "Growth Plan" }),
      });
      setStrategies((prev) => [s, ...prev]);
      setTab("strategy");
    } finally {
      setBusy(false);
    }
  }

  async function updateAction(actionId: string, status: string) {
    const s = await api<Strategy>(`/clients/${clientId}/strategies/actions/${actionId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    setStrategies((prev) => prev.map((x) => (x.id === s.id ? s : x)));
  }

  async function generateContent(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const result = await api<ContentGenerated>(`/clients/${clientId}/content/generate`, {
        method: "POST",
        body: JSON.stringify(contentForm),
      });
      setGenerated(result);
    } finally {
      setBusy(false);
    }
  }

  async function saveContent() {
    if (!generated) return;
    setBusy(true);
    try {
      const post = await api<SocialPost>(`/clients/${clientId}/content/save`, {
        method: "POST",
        body: JSON.stringify({ ...contentForm, ...generated }),
      });
      setPosts((prev) => [post, ...prev]);
      await api(`/clients/${clientId}/content/calendar`, {
        method: "POST",
        body: JSON.stringify({
          title: `${contentForm.platform} ${contentForm.content_type}`,
          platform: contentForm.platform,
          social_post_id: post.id,
          status: "planned",
        }),
      });
      const cal = await api<CalendarItem[]>(`/clients/${clientId}/content/calendar`);
      setCalendar(cal);
    } finally {
      setBusy(false);
    }
  }

  async function createLead(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const lead = await api<Lead>(`/clients/${clientId}/leads`, {
        method: "POST",
        body: JSON.stringify(leadForm),
      });
      setLeads((prev) => [lead, ...prev]);
      setLeadForm({ name: "", email: "", phone: "", source: "Website Form", campaign: "", status: "new" });
    } finally {
      setBusy(false);
    }
  }

  async function moveLead(leadId: string, status: string) {
    const lead = await api<Lead>(`/clients/${clientId}/leads/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    setLeads((prev) => prev.map((l) => (l.id === lead.id ? lead : l)));
  }

  async function askAssistant(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api<{ reply: string }>(`/clients/${clientId}/assistant/chat`, {
        method: "POST",
        body: JSON.stringify({ message: assistantQ }),
      });
      setAssistantA(res.reply);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !client) {
    return <EmptyState title="Client workspace unavailable" description={error || "Client not found"} />;
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/clients" className="text-sm text-[var(--muted)] hover:text-[var(--ink)]">
            ← Clients
          </Link>
          <h1 className="mt-1 font-display text-3xl">{client.business_name}</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {client.industry || "—"} · {client.location || "—"} · Client-aware AI context ready
          </p>
        </div>
        <Button onClick={generateStrategy} disabled={busy}>
          {busy ? "Working..." : "Generate strategy"}
        </Button>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-full px-3 py-1.5 text-sm capitalize transition ${
              tab === t ? "bg-[var(--panel)] text-white" : "bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--ink)]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Business profile" />
            <dl className="space-y-3 text-sm">
              {[
                ["Website", client.website],
                ["Audience", client.target_audience],
                ["Goals", client.marketing_goals],
                ["Brand voice", client.brand_voice],
                ["Channels", client.primary_channels.join(", ")],
                ["KPIs", client.kpis.join(", ")],
              ].map(([k, v]) => (
                <div key={k as string}>
                  <dt className="text-[var(--muted)]">{k}</dt>
                  <dd className="mt-0.5 text-[var(--ink)]">{(v as string) || "Insufficient data."}</dd>
                </div>
              ))}
            </dl>
          </Card>
          <Card>
            <CardHeader title="Workspace snapshot" />
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-[var(--surface-2)] p-4"><div className="text-xs text-[var(--muted)]">Strategies</div><div className="text-2xl">{strategies.length}</div></div>
              <div className="rounded-xl bg-[var(--surface-2)] p-4"><div className="text-xs text-[var(--muted)]">Saved posts</div><div className="text-2xl">{posts.length}</div></div>
              <div className="rounded-xl bg-[var(--surface-2)] p-4"><div className="text-xs text-[var(--muted)]">Leads</div><div className="text-2xl">{leads.length}</div></div>
              <div className="rounded-xl bg-[var(--surface-2)] p-4"><div className="text-xs text-[var(--muted)]">Calendar items</div><div className="text-2xl">{calendar.length}</div></div>
            </div>
          </Card>
        </div>
      )}

      {tab === "performance" && (
        <Card>
          <CardHeader
            title="Performance"
            subtitle="Client analytics with 7/30/90 day comparisons."
            action={
              <Link href={`/analytics`}>
                <Button size="sm" variant="secondary">Open Analytics</Button>
              </Link>
            }
          />
          <p className="text-sm text-[var(--muted)]">
            Charts, CPL/CTR/CVR series, campaign and content performance are available in Analytics.
            Select this client from the Analytics client filter for a scoped view.
          </p>
        </Card>
      )}

      {tab === "content" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader title="Generate content" />
            <form className="grid gap-3" onSubmit={generateContent}>
              {Object.entries(contentForm).map(([key, value]) => (
                <div key={key}>
                  <label className="mb-1 block text-xs uppercase tracking-wide text-[var(--muted)]">{key.replaceAll("_", " ")}</label>
                  <Input value={value} onChange={(e) => setContentForm((f) => ({ ...f, [key]: e.target.value }))} />
                </div>
              ))}
              <Button type="submit" disabled={busy}>{busy ? "Generating..." : "Generate"}</Button>
            </form>
          </Card>
          <Card>
            <CardHeader title="Output" action={generated ? <Button size="sm" onClick={saveContent} disabled={busy}>Save + calendar</Button> : undefined} />
            {generated ? (
              <div className="space-y-3 text-sm">
                <div><div className="text-[var(--muted)]">Hook</div><div>{generated.hook}</div></div>
                <div><div className="text-[var(--muted)]">Main copy</div><div>{generated.main_copy}</div></div>
                <div><div className="text-[var(--muted)]">CTA</div><div>{generated.cta}</div></div>
                <div><div className="text-[var(--muted)]">Visual</div><div>{generated.visual_concept}</div></div>
                {generated.video_concept ? <div><div className="text-[var(--muted)]">Video</div><div>{generated.video_concept}</div></div> : null}
                <div className="flex flex-wrap gap-2">{generated.hashtags.map((h) => <Badge key={h}>{h}</Badge>)}</div>
              </div>
            ) : (
              <p className="text-sm text-[var(--muted)]">Generate content to preview structured output.</p>
            )}
            <div className="mt-6">
              <h4 className="mb-2 font-medium">Saved posts</h4>
              <div className="space-y-2">
                {posts.map((p) => (
                  <div key={p.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                    <div className="font-medium">{p.platform} · {p.content_type}</div>
                    <div className="text-[var(--muted)]">{p.hook}</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </div>
      )}

      {tab === "campaigns" && (
        <Card>
          <CardHeader
            title="Campaigns"
            subtitle="Demo seed rows and live Google Ads sync (Phase 4)."
          />
          {campaigns.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">
              No campaigns yet. Connect Google Ads on Integrations (with this client selected) and Sync.
            </p>
          ) : (
            <div className="space-y-3">
              {campaigns.map((c) => (
                <div
                  key={c.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--line)] px-4 py-3"
                >
                  <div>
                    <div className="font-medium">{c.name}</div>
                    <div className="mt-1 text-xs capitalize text-[var(--muted)]">
                      {c.platform.replaceAll("_", " ")} · {c.status}
                      {c.objective ? ` · ${c.objective}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-sm">
                    <span>{formatCurrency(c.spend)}</span>
                    <span className="text-[var(--muted)]">
                      {formatNumber(Number(c.metrics?.leads ?? c.metrics?.conversions ?? 0))} leads
                    </span>
                    <span className="text-[var(--muted)]">{formatPercent(c.metrics?.ctr ?? null)} CTR</span>
                    <Badge tone={c.data_source === "live" ? "success" : "accent"}>{c.data_source}</Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {tab === "leads" && (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Lead CRM"
              action={
                <div className="flex gap-2">
                  <Button size="sm" variant={leadView === "kanban" ? "primary" : "secondary"} onClick={() => setLeadView("kanban")}>Kanban</Button>
                  <Button size="sm" variant={leadView === "table" ? "primary" : "secondary"} onClick={() => setLeadView("table")}>Table</Button>
                </div>
              }
            />
            <form className="mb-4 grid gap-3 md:grid-cols-3" onSubmit={createLead}>
              <Input placeholder="Name" required value={leadForm.name} onChange={(e) => setLeadForm((f) => ({ ...f, name: e.target.value }))} />
              <Input placeholder="Email" value={leadForm.email} onChange={(e) => setLeadForm((f) => ({ ...f, email: e.target.value }))} />
              <Input placeholder="Source" value={leadForm.source} onChange={(e) => setLeadForm((f) => ({ ...f, source: e.target.value }))} />
              <Input placeholder="Campaign" value={leadForm.campaign} onChange={(e) => setLeadForm((f) => ({ ...f, campaign: e.target.value }))} />
              <Select value={leadForm.status} onChange={(e) => setLeadForm((f) => ({ ...f, status: e.target.value }))}>
                {LEAD_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
              <Button type="submit" disabled={busy}>Add lead</Button>
            </form>

            {leadView === "kanban" ? (
              <div className="grid gap-3 lg:grid-cols-4 xl:grid-cols-7">
                {LEAD_STAGES.map((stage) => (
                  <div key={stage} className="rounded-xl bg-[var(--surface-2)] p-3">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{stage}</div>
                    <div className="space-y-2">
                      {leads.filter((l) => l.status === stage).map((lead) => (
                        <div key={lead.id} className="rounded-lg border border-[var(--line)] bg-white p-3 text-sm">
                          <div className="font-medium">{lead.name}</div>
                          <div className="text-[var(--muted)]">{lead.source || "—"}</div>
                          <div className="mt-1 text-xs">Score {lead.lead_score ?? "—"}/100</div>
                          <Select className="mt-2 h-8" value={lead.status} onChange={(e) => moveLead(lead.id, e.target.value)}>
                            {LEAD_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
                          </Select>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-xs uppercase text-[var(--muted)]">
                    <tr>
                      <th className="px-2 py-2">Name</th>
                      <th className="px-2 py-2">Source</th>
                      <th className="px-2 py-2">Score</th>
                      <th className="px-2 py-2">Status</th>
                      <th className="px-2 py-2">Reasons</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leads.map((lead) => (
                      <tr key={lead.id} className="border-t border-[var(--line)]">
                        <td className="px-2 py-3">{lead.name}<div className="text-xs text-[var(--muted)]">{lead.email}</div></td>
                        <td className="px-2 py-3">{lead.source}</td>
                        <td className="px-2 py-3">{lead.lead_score ?? "—"}</td>
                        <td className="px-2 py-3">
                          <Select className="h-8" value={lead.status} onChange={(e) => moveLead(lead.id, e.target.value)}>
                            {LEAD_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
                          </Select>
                        </td>
                        <td className="px-2 py-3 text-[var(--muted)]">
                          {(lead.score_explanation?.reasons || []).slice(0, 2).join(" · ") || "Insufficient data."}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === "strategy" && (
        <div className="space-y-4">
          {!latestStrategy ? (
            <EmptyState title="No strategy yet" description="Generate a strategy from client context and available metrics." actionLabel="Generate" onAction={generateStrategy} />
          ) : (
            <>
              <Card>
                <CardHeader title={latestStrategy.title} subtitle="AI Strategy Engine output" />
                <div className="grid gap-4 md:grid-cols-2 text-sm">
                  <div><div className="text-[var(--muted)]">Current situation</div><p className="mt-1">{latestStrategy.current_situation}</p></div>
                  <div><div className="text-[var(--muted)]">What is happening?</div><p className="mt-1">{latestStrategy.what_is_happening}</p></div>
                  <div><div className="text-[var(--muted)]">Key problems</div><ul className="mt-1 list-disc pl-5">{latestStrategy.key_problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
                  <div><div className="text-[var(--muted)]">Opportunities</div><ul className="mt-1 list-disc pl-5">{latestStrategy.opportunities.map((p) => <li key={p}>{p}</li>)}</ul></div>
                </div>
                <p className="mt-4 text-sm"><span className="font-medium">Strategy:</span> {latestStrategy.strategy_summary}</p>
              </Card>
              <Card>
                <CardHeader title="Action plan" subtitle="Approval workflow: Pending → Approved / Rejected → Completed" />
                <div className="space-y-3">
                  {latestStrategy.actions.map((action) => (
                    <div key={action.id} className="rounded-xl border border-[var(--line)] p-4">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <Badge tone={action.priority}>{action.priority}</Badge>
                        <Badge>{action.status}</Badge>
                        <span className="text-xs text-[var(--muted)]">{action.channel} · {action.estimated_effort} effort</span>
                      </div>
                      <div className="font-medium">{action.action}</div>
                      <p className="mt-1 text-sm text-[var(--muted)]">{action.expected_outcome}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(["approved", "rejected", "completed", "pending"] as const).map((status) => (
                          <Button key={status} size="sm" variant="secondary" onClick={() => updateAction(action.id, status)}>
                            {status}
                          </Button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            </>
          )}
        </div>
      )}

      {tab === "reports" && (
        <Card>
          <CardHeader
            title="Weekly reports"
            action={
              <Link href="/reports">
                <Button size="sm">Open Reports</Button>
              </Link>
            }
          />
          <p className="text-sm text-[var(--muted)]">
            Generate AI weekly reports with executive summary, metrics, content/campaign rankings, and PDF export.
          </p>
        </Card>
      )}

      {tab === "competitors" && (
        <Card>
          <CardHeader
            title="Competitors"
            action={
              <Link href="/competitors">
                <Button size="sm" variant="secondary">Manage</Button>
              </Link>
            }
          />
          <ul className="list-disc pl-5 text-sm">
            {(client.competitors.length ? client.competitors : ["Insufficient data."]).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </Card>
      )}

      {tab === "integrations" && (
        <Card>
          <CardHeader title="Integrations" subtitle="Architecture stubs — statuses are honest." />
          <div className="space-y-3">
            {integrations.map((item) => (
              <div key={item.provider} className="flex items-center justify-between rounded-xl border border-[var(--line)] p-3">
                <div>
                  <div className="font-medium capitalize">{item.provider.replaceAll("_", " ")}</div>
                  <div className="text-sm text-[var(--muted)]">{item.message}</div>
                </div>
                <StatusDot status={item.status} label={item.status.replaceAll("_", " ")} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {tab === "assistant" && (
        <Card>
          <CardHeader title="AI Assistant" subtitle="Client-aware. Uses stored context and available analytics only." />
          <form className="space-y-3" onSubmit={askAssistant}>
            <Textarea value={assistantQ} onChange={(e) => setAssistantQ(e.target.value)} />
            <Button type="submit" disabled={busy}>{busy ? "Thinking..." : "Ask"}</Button>
          </form>
          {assistantA ? <div className="mt-4 rounded-xl bg-[var(--surface-2)] p-4 text-sm">{assistantA}</div> : null}
        </Card>
      )}
    </div>
  );
}

export default function ClientWorkspacePage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <ClientWorkspace />
    </Suspense>
  );
}
