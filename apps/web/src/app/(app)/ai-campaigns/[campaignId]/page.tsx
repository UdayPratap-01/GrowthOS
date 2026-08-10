"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ApprovalPanel } from "@/components/campaign/ApprovalPanel";
import { ConceptCard } from "@/components/campaign/ConceptCard";
import { DataLimitations } from "@/components/campaign/DataLimitations";
import { GenerationProgress } from "@/components/campaign/GenerationProgress";
import { StrategyPanel } from "@/components/campaign/StrategyPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { api, errorMessage } from "@/lib/api";
import {
  approveCampaign,
  archiveConcept,
  createVariations,
  fetchPackage,
  regenerateConcept,
  rejectCampaign,
  reviewStatusLabel,
  reviewStatusTone,
} from "@/lib/campaign-generation";
import type { User } from "@/types";
import type { CampaignPackage, VariationAxis } from "@/types/campaign-generation";

const TABS = ["strategy", "creative", "structure"] as const;
type Tab = (typeof TABS)[number];

/**
 * Campaign preview and approval.
 *
 * The whole package in one place: strategy, brief, concepts with their real
 * renders, variations, ad sets and ads. While the run is still working the page
 * refreshes on an interval so media tiles fill in as the worker stores them.
 *
 * Nothing here can publish. Approval records who signed off and moves the
 * campaign to READY_TO_PUBLISH; sending it to an ad platform is a later phase,
 * and the page says so rather than leaving it to be assumed.
 */
export default function CampaignPreviewPage() {
  const params = useParams<{ campaignId: string }>();
  const campaignId = params.campaignId;

  const [pkg, setPkg] = useState<CampaignPackage | null>(null);
  const [role, setRole] = useState<string>("viewer");
  const [tab, setTab] = useState<Tab>("strategy");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    try {
      const data = await fetchPackage(campaignId);
      if (mounted.current) setPkg(data);
      return data;
    } catch (err) {
      if (mounted.current) setError(errorMessage(err));
      return null;
    }
  }, [campaignId]);

  useEffect(() => {
    mounted.current = true;
    (async () => {
      await Promise.all([
        load(),
        api<User>("/auth/me")
          .then((me) => mounted.current && setRole(me.role))
          .catch(() => undefined),
      ]);
      if (mounted.current) setLoading(false);
    })();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const runInFlight = Boolean(pkg?.run && !pkg.run.terminal);

  useEffect(() => {
    if (!runInFlight) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [runInFlight, load]);

  const canWrite = role !== "viewer";

  const adSetsWithAds = useMemo(() => {
    if (!pkg) return [];
    return pkg.ad_sets.map((adSet) => ({
      adSet,
      ads: pkg.ads.filter((ad) => ad.ad_set_id === adSet.id),
    }));
  }, [pkg]);

  async function guarded(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
      setNotice(message);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  function onCreateVariations(conceptId: string, axes: VariationAxis[]) {
    return guarded(
      () => createVariations(conceptId, { count: 2, axes, generate_media: false }),
      "Variations created.",
    );
  }

  function onRegenerate(conceptId: string, kind: "image" | "video") {
    return guarded(
      () =>
        regenerateConcept(conceptId, {
          image_quantity: kind === "image" ? 1 : 0,
          video_quantity: kind === "video" ? 1 : 0,
        }),
      `Regeneration queued. The tile updates when the ${kind} is stored.`,
    );
  }

  function onArchiveConcept(conceptId: string, archived: boolean) {
    return guarded(
      () => archiveConcept(conceptId, archived),
      archived ? "Concept archived." : "Concept restored.",
    );
  }

  function onApprove(comment: string) {
    return guarded(
      () => approveCampaign(campaignId, comment || undefined),
      "Approved. The package is ready to publish; nothing was sent to an ad platform.",
    );
  }

  function onReject(reason: string) {
    return guarded(() => rejectCampaign(campaignId, reason), "Rejected. The reason was recorded.");
  }

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!pkg) {
    return (
      <EmptyState
        title="Campaign not available"
        description={error || "This campaign does not exist in your organization."}
      />
    );
  }

  const campaign = pkg.campaign;
  const brief = pkg.brief;

  return (
    <div className="space-y-6 animate-rise">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-display text-3xl">{campaign?.name || "Campaign"}</h1>
            {campaign ? (
              <Badge tone={reviewStatusTone(campaign.review_status)}>
                {reviewStatusLabel(campaign.review_status)}
              </Badge>
            ) : null}
            {campaign?.data_source === "demo" ? <Badge tone="demo">Demo</Badge> : null}
          </div>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {campaign?.platform} · {campaign?.objective?.replaceAll("_", " ") || "objective not set"}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/ai-campaigns">
            <Button variant="secondary">All AI campaigns</Button>
          </Link>
          <Link href="/ai-campaigns/new">
            <Button variant="ghost">Generate another</Button>
          </Link>
        </div>
      </div>

      {notice ? (
        <div className="rounded-xl border border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-3 text-sm text-[var(--accent-ink)]">
          {notice}
        </div>
      ) : null}
      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      {pkg.run && !pkg.run.terminal ? (
        <GenerationProgress run={pkg.run} title="Still generating" />
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Overview" subtitle="Budget as requested. The AI does not choose spend." />
            <dl className="grid gap-4 sm:grid-cols-3">
              <Stat label="Platform" value={campaign?.platform} />
              <Stat label="Objective" value={campaign?.objective?.replaceAll("_", " ")} />
              <Stat label="Review status" value={reviewStatusLabel(campaign?.review_status)} />
              <Stat
                label="Daily budget"
                value={campaign?.daily_budget ? `${campaign.currency} ${campaign.daily_budget}` : null}
              />
              <Stat
                label="Total budget"
                value={campaign?.total_budget ? `${campaign.currency} ${campaign.total_budget}` : null}
              />
              <Stat
                label="Monthly budget"
                value={
                  campaign?.monthly_budget ? `${campaign.currency} ${campaign.monthly_budget}` : null
                }
              />
              <Stat label="Audience" value={campaign?.audience} className="sm:col-span-3" />
            </dl>
          </Card>

          <div className="flex gap-2">
            {TABS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setTab(option)}
                className={`rounded-full px-3 py-1.5 text-sm capitalize transition ${
                  tab === option
                    ? "bg-[var(--panel)] text-white"
                    : "bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--ink)]"
                }`}
              >
                {option === "structure" ? "Ad sets & ads" : option}
              </button>
            ))}
          </div>

          {tab === "strategy" ? (
            <div className="space-y-6">
              {pkg.strategy ? (
                <StrategyPanel strategy={pkg.strategy} />
              ) : (
                <EmptyState
                  title="No strategy recorded"
                  description="This campaign has no stored strategy document."
                />
              )}
              {brief ? <BriefPanel brief={brief} /> : null}
            </div>
          ) : null}

          {tab === "creative" ? (
            <div className="space-y-6">
              {pkg.concepts.length === 0 ? (
                <EmptyState
                  title="No creative concepts"
                  description="Concepts are produced during generation. If the run failed, generate again."
                />
              ) : (
                pkg.concepts.map((concept, index) => (
                  <ConceptCard
                    key={concept.id}
                    concept={concept}
                    index={index}
                    busy={busy}
                    canWrite={canWrite}
                    onCreateVariations={onCreateVariations}
                    onRegenerate={onRegenerate}
                    onArchive={onArchiveConcept}
                  />
                ))
              )}
            </div>
          ) : null}

          {tab === "structure" ? (
            <Card>
              <CardHeader
                title="Campaign structure"
                subtitle="Proposed ad sets and ads. Nothing exists on any ad platform."
              />
              {adSetsWithAds.length === 0 ? (
                <p className="text-sm text-[var(--muted)]">No structure was built for this campaign.</p>
              ) : (
                <div className="space-y-4">
                  {adSetsWithAds.map(({ adSet, ads }) => (
                    <div key={adSet.id} className="rounded-xl border border-[var(--line)] p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="font-medium">{adSet.name}</div>
                        <div className="flex items-center gap-2">
                          {adSet.daily_budget ? (
                            <Badge tone="low">Daily {adSet.daily_budget}</Badge>
                          ) : null}
                          <Badge>{adSet.status}</Badge>
                        </div>
                      </div>
                      <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                        {adSet.audience ? (
                          <div>
                            <span className="text-[var(--muted)]">Audience: </span>
                            {adSet.audience}
                          </div>
                        ) : null}
                        {adSet.optimization ? (
                          <div>
                            <span className="text-[var(--muted)]">Optimization: </span>
                            {adSet.optimization}
                          </div>
                        ) : null}
                      </div>
                      {adSet.placements.length ? (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {adSet.placements.map((placement) => (
                            <Badge key={placement}>{placement}</Badge>
                          ))}
                        </div>
                      ) : null}

                      <div className="mt-3 space-y-2">
                        {ads.length === 0 ? (
                          <p className="text-sm text-[var(--muted)]">No ads in this ad set.</p>
                        ) : (
                          ads.map((ad) => (
                            <div
                              key={ad.id}
                              className="rounded-lg bg-[var(--surface-2)] px-3 py-2 text-sm"
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="font-medium">{ad.name}</span>
                                <Badge>{ad.status}</Badge>
                              </div>
                              {ad.headline ? <div className="mt-1">{ad.headline}</div> : null}
                              {ad.primary_text ? (
                                <p className="mt-1 text-[var(--muted)]">{ad.primary_text}</p>
                              ) : null}
                              <div className="mt-1 flex flex-wrap gap-3 text-xs text-[var(--muted)]">
                                {ad.cta ? <span>CTA: {ad.cta}</span> : null}
                                {ad.destination ? <span>{ad.destination}</span> : null}
                                {ad.creative_asset_id ? <span>creative attached</span> : null}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          ) : null}
        </div>

        <div className="space-y-6">
          {pkg.approval ? (
            <ApprovalPanel
              approval={pkg.approval}
              publishingNote={pkg.publishing_note}
              busy={busy}
              onApprove={onApprove}
              onReject={onReject}
            />
          ) : null}

          <DataLimitations items={pkg.data_limitations} title="What the AI did not know" />

          {pkg.media ? (
            <Card>
              <CardHeader title="Media providers" subtitle={pkg.media.message} />
              <div className="flex flex-wrap gap-2">
                <Badge tone={pkg.media.image_configured ? "success" : "warning"}>
                  Image: {pkg.media.image_provider}
                  {pkg.media.image_configured ? "" : " — NOT CONFIGURED"}
                </Badge>
                <Badge tone={pkg.media.video_configured ? "success" : "warning"}>
                  Video: {pkg.media.video_provider}
                  {pkg.media.video_configured ? "" : " — NOT CONFIGURED"}
                </Badge>
                <Badge tone="low">Storage: {pkg.media.storage_backend}</Badge>
                {pkg.media.demo_mode ? <Badge tone="demo">Demo mode</Badge> : null}
              </div>
            </Card>
          ) : null}

          {pkg.run ? (
            <Card>
              <CardHeader title="Generation run" subtitle={`Started ${new Date(pkg.run.created_at).toLocaleString()}`} />
              <dl className="space-y-2 text-sm">
                <Row term="Status" value={pkg.run.status.replaceAll("_", " ")} />
                <Row term="Concepts" value={String(pkg.run.concept_quantity)} />
                <Row term="Images requested" value={String(pkg.run.image_quantity)} />
                <Row term="Videos requested" value={String(pkg.run.video_quantity)} />
                <Row term="Variations requested" value={String(pkg.run.variation_quantity)} />
              </dl>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function BriefPanel({ brief }: { brief: NonNullable<CampaignPackage["brief"]> }) {
  return (
    <Card>
      <CardHeader title="Campaign brief" subtitle={brief.campaign_name} />
      <dl className="grid gap-4 sm:grid-cols-2">
        <Stat label="Offer" value={brief.offer} />
        <Stat label="Audience" value={brief.audience} />
        <Stat label="Value proposition" value={brief.value_proposition} />
        <Stat label="Messaging angle" value={brief.messaging_angle} />
        <Stat label="Tone" value={brief.tone} />
        <Stat label="CTA" value={brief.cta} />
        <Stat label="Creative direction" value={brief.creative_direction} className="sm:col-span-2" />
      </dl>

      {brief.pain_points.length ? (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Pain points
          </div>
          <ul className="mt-2 space-y-1 text-sm">
            {brief.pain_points.map((point, index) => (
              <li key={`${index}-${point}`}>· {point}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {brief.brand_constraints.length ? (
        <div className="mt-4 flex flex-wrap gap-1">
          {brief.brand_constraints.map((constraint) => (
            <Badge key={constraint}>{constraint}</Badge>
          ))}
        </div>
      ) : null}

      {brief.success_metrics.length ? (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Success metrics
          </div>
          <ul className="mt-2 space-y-1 text-sm">
            {brief.success_metrics.map((metric, index) => (
              <li key={`${index}-${metric}`}>· {metric}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4">
        <DataLimitations items={brief.data_limitations} />
      </div>
    </Card>
  );
}

function Stat({
  label,
  value,
  className,
}: {
  label: string;
  value: string | null | undefined;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</dt>
      <dd className="mt-1 text-sm">{value || <span className="text-[var(--muted)]">Not set</span>}</dd>
    </div>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-[var(--muted)]">{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}
