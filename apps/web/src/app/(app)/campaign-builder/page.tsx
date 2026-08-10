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
import { AutopilotRun, Client } from "@/types";

type BuildResult = {
  run: AutopilotRun;
  action_ids: string[];
  plan: Record<string, unknown>;
  message: string;
};

const PLATFORMS = ["meta", "instagram", "google_ads", "youtube"];

export default function CampaignBuilderPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [objective, setObjective] = useState("Generate Leads");
  const [budget, setBudget] = useState("500");
  const [duration, setDuration] = useState("30");
  const [offer, setOffer] = useState("");
  const [audience, setAudience] = useState("");
  const [location, setLocation] = useState("");
  const [cta, setCta] = useState("Learn More");
  const [images, setImages] = useState("5");
  const [videos, setVideos] = useState("3");
  const [variations, setVariations] = useState("10");
  const [platforms, setPlatforms] = useState<string[]>(["meta", "instagram"]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BuildResult | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const c = await api<Client[]>("/clients");
        setClients(c);
        if (c[0]) setClientId(c[0].id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load clients");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function togglePlatform(p: string) {
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  async function build() {
    if (!clientId || platforms.length === 0) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api<BuildResult>("/autopilot/campaigns/build", {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          objective,
          budget: Number(budget),
          duration_days: Number(duration),
          offer: offer || null,
          target_audience: audience || null,
          location: location || null,
          platforms,
          image_quantity: Number(images),
          video_quantity: Number(videos),
          variation_quantity: Number(variations),
          cta: cta || null,
          campaign_goal: objective,
        }),
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton className="h-80 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">AI Campaign Builder</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Builds strategy, creatives, and structured actions — never claims live publish without platform confirmation.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/autopilot"><Button variant="secondary">Autopilot</Button></Link>
          <Link href="/approvals"><Button variant="ghost">Approvals</Button></Link>
        </div>
      </div>

      {error ? <EmptyState title="Builder notice" description={error} /> : null}

      <Card>
        <CardHeader title="Campaign brief" subtitle="Client-scoped. Budget proposals respect autonomy safety caps." />
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Client</span>
            <Select className="mt-1 w-full" value={clientId} onChange={(e) => setClientId(e.target.value)}>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>{c.business_name}</option>
              ))}
            </Select>
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Objective</span>
            <Input className="mt-1" value={objective} onChange={(e) => setObjective(e.target.value)} />
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
            <span className="text-[var(--muted)]">Offer / Product</span>
            <Input className="mt-1" value={offer} onChange={(e) => setOffer(e.target.value)} placeholder="Summer membership" />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Target audience</span>
            <Input className="mt-1" value={audience} onChange={(e) => setAudience(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Location</span>
            <Input className="mt-1" value={location} onChange={(e) => setLocation(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">CTA</span>
            <Input className="mt-1" value={cta} onChange={(e) => setCta(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Images</span>
            <Input className="mt-1" value={images} onChange={(e) => setImages(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Videos</span>
            <Input className="mt-1" value={videos} onChange={(e) => setVideos(e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="text-[var(--muted)]">Variations</span>
            <Input className="mt-1" value={variations} onChange={(e) => setVariations(e.target.value)} />
          </label>
        </div>
        <div className="mt-4">
          <div className="text-sm text-[var(--muted)]">Platforms</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {PLATFORMS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => togglePlatform(p)}
                className={`rounded-lg border px-3 py-1.5 text-sm ${
                  platforms.includes(p)
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "border-[var(--line)]"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-6">
          <Button disabled={busy || !clientId} onClick={build}>
            {busy ? "Building…" : "BUILD CAMPAIGN WITH AI"}
          </Button>
        </div>
      </Card>

      {result ? (
        <Card>
          <CardHeader
            title="Build progress"
            subtitle={result.message}
            action={
              <div className="flex gap-2">
                {result.run.demo_mode ? <Badge tone="demo">DEMO DATA</Badge> : null}
                <Badge
                  tone={
                    result.run.status === "failed"
                      ? "danger"
                      : result.run.status === "completed"
                        ? "success"
                        : "accent"
                  }
                >
                  {result.run.status}
                </Badge>
              </div>
            }
          />
          <p className="mb-3 text-xs text-[var(--muted)]">
            Lifecycle: draft → pending approval → approved → executing → published / failed.
            This build creates structured actions; live publishing only happens after approval
            and a confirmed platform response.
          </p>
          <ol className="space-y-2">
            {(result.run.steps || []).map((s) => (
              <li key={s.key} className="flex items-start gap-3 rounded-xl border border-[var(--line)] px-3 py-2 text-sm">
                <span className="mt-0.5 w-5 shrink-0">
                  {s.status === "completed" ? "✓" : s.status === "blocked" || s.status === "failed" ? "!" : s.status === "running" ? "⏳" : "○"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="font-medium">{s.label}</div>
                    <Badge
                      tone={
                        s.status === "completed"
                          ? "success"
                          : s.status === "failed" || s.status === "blocked"
                            ? "danger"
                            : s.status === "running"
                              ? "accent"
                              : "default"
                      }
                    >
                      {s.status}
                    </Badge>
                  </div>
                  {s.detail ? <div className="text-[var(--muted)]">{s.detail}</div> : null}
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/approvals"><Button size="sm">Review approvals</Button></Link>
            <Link href="/creative-library"><Button size="sm" variant="secondary">Creative library</Button></Link>
            <Link href="/ai-activity"><Button size="sm" variant="ghost">AI activity</Button></Link>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
