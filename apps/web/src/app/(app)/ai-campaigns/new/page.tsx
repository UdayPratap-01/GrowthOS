"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DataLimitations } from "@/components/campaign/DataLimitations";
import { GenerationProgress } from "@/components/campaign/GenerationProgress";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { api, errorMessage } from "@/lib/api";
import { fetchGeneratorOptions, pollRun, startGeneration } from "@/lib/campaign-generation";
import type { Client } from "@/types";
import type {
  CampaignGenerationRun,
  CampaignGeneratorOptions,
} from "@/types/campaign-generation";

/**
 * "Create Campaign with AI".
 *
 * Platforms, objectives, formats and quantity ceilings all come from
 * `/campaign-generation/options` rather than being listed here, so the form can
 * never offer a platform the backend does not support or a quantity it will
 * clamp. Connection state is reported exactly as the server sees it: a platform
 * is shown as connected only when an integration confirms it, and no platform is
 * ever presented as publishable in this phase.
 */
export default function CampaignGeneratorPage() {
  const router = useRouter();
  const [options, setOptions] = useState<CampaignGeneratorOptions | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [run, setRun] = useState<CampaignGenerationRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Regenerated per submitted run so a double-click cannot start two runs, while
  // a deliberate second generation still can.
  const [idempotencyKey, setIdempotencyKey] = useState(() => newKey());

  const [form, setForm] = useState({
    client_id: "",
    platform: "meta",
    objective: "lead_generation",
    campaign_name: "",
    total_budget: "",
    daily_budget: "",
    monthly_budget: "",
    currency: "USD",
    duration_days: 30,
    offer: "",
    audience: "",
    tone: "",
    cta: "",
    concept_quantity: 3,
    image_quantity: 3,
    video_quantity: 0,
    variation_quantity: 2,
  });
  const [ratios, setRatios] = useState<string[]>([]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [opts, clientList] = await Promise.all([
          fetchGeneratorOptions(),
          api<Client[]>("/clients"),
        ]);
        if (!mounted) return;
        setOptions(opts);
        setClients(clientList);
        setForm((current) => ({
          ...current,
          client_id: current.client_id || clientList[0]?.id || "",
          platform: current.platform || opts.platforms[0]?.key || "meta",
          image_quantity: Math.min(current.image_quantity, opts.limits.max_images),
          video_quantity: Math.min(current.video_quantity, opts.limits.max_videos),
          concept_quantity: Math.min(current.concept_quantity, opts.limits.max_concepts),
          variation_quantity: Math.min(current.variation_quantity, opts.limits.max_variations),
        }));
      } catch (err) {
        if (mounted) setError(errorMessage(err));
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
      abortRef.current?.abort();
    };
  }, []);

  const platform = useMemo(
    () => options?.platforms.find((p) => p.key === form.platform) || null,
    [options, form.platform],
  );
  const objective = useMemo(
    () => options?.objectives.find((o) => o.key === form.objective) || null,
    [options, form.objective],
  );

  // Reset formats when the platform changes: 9:16 on a platform that does not
  // accept it would be a request the server has to reject.
  useEffect(() => {
    if (!platform) return;
    setRatios((current) => current.filter((r) => platform.aspect_ratios.includes(r)));
  }, [platform]);

  const videoUnavailable = !options?.media.video_configured || !platform?.supports_video;

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function clamp(value: number, max: number) {
    if (Number.isNaN(value)) return 0;
    return Math.max(0, Math.min(value, max));
  }

  async function submit() {
    if (!options || !form.client_id) return;
    setSubmitting(true);
    setError(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const accepted = await startGeneration({
        client_id: form.client_id,
        platform: form.platform,
        objective: form.objective,
        campaign_name: form.campaign_name.trim() || null,
        total_budget: form.total_budget.trim() || null,
        daily_budget: form.daily_budget.trim() || null,
        monthly_budget: form.monthly_budget.trim() || null,
        currency: form.currency || "USD",
        duration_days: form.duration_days,
        offer: form.offer.trim() || null,
        audience: form.audience.trim() || null,
        tone: form.tone.trim() || null,
        cta: form.cta.trim() || null,
        concept_quantity: form.concept_quantity,
        image_quantity: form.image_quantity,
        video_quantity: videoUnavailable ? 0 : form.video_quantity,
        variation_quantity: form.variation_quantity,
        aspect_ratios: ratios,
        idempotency_key: idempotencyKey,
      });
      setRun(accepted);
      setIdempotencyKey(newKey());

      const finished = await pollRun(accepted.id, {
        signal: controller.signal,
        onUpdate: setRun,
      });

      if (finished.campaign_id && finished.status === "READY_FOR_REVIEW") {
        router.push(`/ai-campaigns/${finished.campaign_id}`);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (error && !options) {
    return <EmptyState title="Campaign generator unavailable" description={error} />;
  }
  if (!options) return null;
  if (clients.length === 0) {
    return (
      <EmptyState
        title="Add a client first"
        description="The campaign engine builds strategy from a client's stored business context. Create a client to generate against."
        actionLabel="Go to clients"
        onAction={() => router.push("/clients")}
      />
    );
  }

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl">Create Campaign with AI</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Generates strategy, copy, creative concepts and real media. Nothing is published and no
            ad budget is spent.
          </p>
        </div>
        <Link href="/ai-campaigns">
          <Button variant="secondary">All AI campaigns</Button>
        </Link>
      </div>

      <ProviderBanner media={options.media} />

      {run ? (
        <GenerationProgress run={run} />
      ) : null}

      {run && run.data_limitations.length ? (
        <DataLimitations items={run.data_limitations} />
      ) : null}

      {run?.campaign_id && run.status === "READY_FOR_REVIEW" ? (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-display text-lg">Campaign ready for review</div>
              <p className="text-sm text-[var(--muted)]">
                Open the preview to read the strategy and approve or reject the package.
              </p>
            </div>
            <Link href={`/ai-campaigns/${run.campaign_id}`}>
              <Button>Open preview</Button>
            </Link>
          </div>
        </Card>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <Card>
          <CardHeader title="Campaign inputs" subtitle="Only the client and platform are required." />

          <div className="grid gap-4 sm:grid-cols-2">
            <Labelled label="Client">
              <Select
                value={form.client_id}
                disabled={submitting}
                onChange={(e) => update("client_id", e.target.value)}
              >
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.business_name}
                  </option>
                ))}
              </Select>
            </Labelled>

            <Labelled label="Platform">
              <Select
                value={form.platform}
                disabled={submitting}
                onChange={(e) => update("platform", e.target.value)}
              >
                {options.platforms.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Labelled>

            <Labelled label="Objective" className="sm:col-span-2">
              <Select
                value={form.objective}
                disabled={submitting}
                onChange={(e) => update("objective", e.target.value)}
              >
                {options.objectives.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </Select>
              {objective ? (
                <p className="mt-1 text-xs text-[var(--muted)]">{objective.description}</p>
              ) : null}
            </Labelled>

            <Labelled label="Campaign name (optional)" className="sm:col-span-2">
              <Input
                value={form.campaign_name}
                disabled={submitting}
                placeholder="Left blank, the AI names it"
                onChange={(e) => update("campaign_name", e.target.value)}
              />
            </Labelled>

            <Labelled label="Total budget">
              <Input
                value={form.total_budget}
                inputMode="decimal"
                disabled={submitting}
                placeholder="e.g. 6000"
                onChange={(e) => update("total_budget", e.target.value)}
              />
            </Labelled>
            <Labelled label="Currency">
              <Input
                value={form.currency}
                disabled={submitting}
                onChange={(e) => update("currency", e.target.value.toUpperCase().slice(0, 8))}
              />
            </Labelled>
            <Labelled label="Daily budget">
              <Input
                value={form.daily_budget}
                inputMode="decimal"
                disabled={submitting}
                placeholder="e.g. 200"
                onChange={(e) => update("daily_budget", e.target.value)}
              />
            </Labelled>
            <Labelled label="Monthly budget">
              <Input
                value={form.monthly_budget}
                inputMode="decimal"
                disabled={submitting}
                placeholder="e.g. 6000"
                onChange={(e) => update("monthly_budget", e.target.value)}
              />
            </Labelled>

            <Labelled label="Duration (days)">
              <Input
                type="number"
                min={1}
                max={365}
                value={form.duration_days}
                disabled={submitting}
                onChange={(e) => update("duration_days", clamp(Number(e.target.value), 365) || 1)}
              />
            </Labelled>
            <Labelled label="CTA">
              <Input
                value={form.cta}
                disabled={submitting}
                placeholder="e.g. Book a call"
                onChange={(e) => update("cta", e.target.value)}
              />
            </Labelled>

            <Labelled label="Offer" className="sm:col-span-2">
              <Textarea
                rows={2}
                value={form.offer}
                disabled={submitting}
                placeholder="What is being promoted? Free audit, 20% off first month, demo…"
                onChange={(e) => update("offer", e.target.value)}
              />
            </Labelled>
            <Labelled label="Audience" className="sm:col-span-2">
              <Textarea
                rows={2}
                value={form.audience}
                disabled={submitting}
                placeholder="Left blank, the client's stored target audience is used."
                onChange={(e) => update("audience", e.target.value)}
              />
            </Labelled>
            <Labelled label="Tone" className="sm:col-span-2">
              <Input
                value={form.tone}
                disabled={submitting}
                placeholder="Left blank, the client's brand voice is used."
                onChange={(e) => update("tone", e.target.value)}
              />
            </Labelled>
          </div>

          {error ? <p className="mt-4 text-sm text-red-600">{error}</p> : null}

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Button disabled={submitting || !form.client_id} onClick={submit}>
              {submitting ? "Generating…" : "Generate campaign"}
            </Button>
            <span className="text-xs text-[var(--muted)]">
              Runs in the background. You can leave this page and reopen the run from AI campaigns.
            </span>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Creative volume"
              subtitle={`Server limits: ${options.limits.max_concepts} concepts, ${options.limits.max_images} images, ${options.limits.max_videos} videos, ${options.limits.max_variations} variations.`}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <Labelled label="Concepts">
                <Input
                  type="number"
                  min={1}
                  max={options.limits.max_concepts}
                  value={form.concept_quantity}
                  disabled={submitting}
                  onChange={(e) =>
                    update(
                      "concept_quantity",
                      clamp(Number(e.target.value), options.limits.max_concepts) || 1,
                    )
                  }
                />
              </Labelled>
              <Labelled label="Variations">
                <Input
                  type="number"
                  min={0}
                  max={options.limits.max_variations}
                  value={form.variation_quantity}
                  disabled={submitting}
                  onChange={(e) =>
                    update(
                      "variation_quantity",
                      clamp(Number(e.target.value), options.limits.max_variations),
                    )
                  }
                />
              </Labelled>
              <Labelled label="Images">
                <Input
                  type="number"
                  min={0}
                  max={options.limits.max_images}
                  value={form.image_quantity}
                  disabled={submitting || !options.media.image_configured}
                  onChange={(e) =>
                    update("image_quantity", clamp(Number(e.target.value), options.limits.max_images))
                  }
                />
                {!options.media.image_configured ? (
                  <p className="mt-1 text-xs text-amber-700">
                    No image provider configured — images will report NOT_CONFIGURED.
                  </p>
                ) : null}
              </Labelled>
              <Labelled label="Videos">
                <Input
                  type="number"
                  min={0}
                  max={options.limits.max_videos}
                  value={videoUnavailable ? 0 : form.video_quantity}
                  disabled={submitting || videoUnavailable}
                  onChange={(e) =>
                    update("video_quantity", clamp(Number(e.target.value), options.limits.max_videos))
                  }
                />
                {videoUnavailable ? (
                  <p className="mt-1 text-xs text-amber-700">
                    {options.media.video_configured
                      ? `${platform?.label || "This platform"} does not take video in this configuration.`
                      : "No video provider configured — videos are disabled."}
                  </p>
                ) : null}
              </Labelled>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Formats"
              subtitle={
                platform
                  ? `Supported by ${platform.label}. None selected uses the platform default.`
                  : "Select a platform first."
              }
            />
            <div className="flex flex-wrap gap-2">
              {(platform?.aspect_ratios || []).map((ratio) => {
                const spec = options.aspect_ratios.find((r) => r.key === ratio);
                const selected = ratios.includes(ratio);
                return (
                  <button
                    key={ratio}
                    type="button"
                    disabled={submitting}
                    onClick={() =>
                      setRatios((current) =>
                        current.includes(ratio)
                          ? current.filter((r) => r !== ratio)
                          : [...current, ratio].slice(0, 4),
                      )
                    }
                    className={`rounded-full px-3 py-1.5 text-sm transition ${
                      selected
                        ? "bg-[var(--panel)] text-white"
                        : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--ink)]"
                    }`}
                    title={spec?.usage}
                  >
                    {ratio}
                  </button>
                );
              })}
            </div>
            {platform ? (
              <p className="mt-3 text-xs text-[var(--muted)]">
                Default image format {platform.default_image_ratio}
                {platform.supports_video ? `, video ${platform.default_video_ratio}` : ""}. Copy is
                capped at {platform.headline_max_chars} characters for headlines and{" "}
                {platform.primary_text_max_chars} for primary text.
              </p>
            ) : null}
          </Card>

          <Card>
            <CardHeader title="Platform status" subtitle="Reported by this organization's integrations." />
            <ul className="space-y-2 text-sm">
              {options.platforms.map((option) => (
                <li key={option.key} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{option.label}</span>
                  <Badge tone={option.connected ? "success" : "default"}>
                    {option.connection_status.replaceAll("_", " ")}
                  </Badge>
                  {option.publishing_supported ? null : (
                    <span className="text-xs text-[var(--muted)]">publishing out of scope</span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ProviderBanner({ media }: { media: CampaignGeneratorOptions["media"] }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={media.image_configured ? "success" : "warning"}>
          Image: {media.image_provider}
          {media.image_configured ? "" : " — NOT CONFIGURED"}
        </Badge>
        <Badge tone={media.video_configured ? "success" : "warning"}>
          Video: {media.video_provider}
          {media.video_configured ? "" : " — NOT CONFIGURED"}
        </Badge>
        <Badge tone="low">Storage: {media.storage_backend}</Badge>
        {media.demo_mode ? <Badge tone="demo">Demo mode</Badge> : null}
      </div>
      <p className="mt-2 text-xs text-[var(--muted)]">{media.message}</p>
    </Card>
  );
}

function Labelled({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block text-sm ${className || ""}`}>
      <span className="text-[var(--muted)]">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
