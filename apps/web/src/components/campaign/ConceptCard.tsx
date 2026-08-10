"use client";

import { useState } from "react";
import { AssetTile } from "@/components/campaign/AssetTile";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { axisLabel } from "@/lib/campaign-generation";
import type { CreativeConcept, VariationAxis } from "@/types/campaign-generation";

const AXES: VariationAxis[] = [
  "hook",
  "visual",
  "offer",
  "cta",
  "tone",
  "composition",
  "format",
  "audience_angle",
];

/**
 * One marketing concept: the copy, the visual direction, its renders, and its
 * variations.
 *
 * The prompts are shown rather than hidden. A reviewer who can read the prompt
 * can tell whether the concept was grounded in this client's actual offer or is
 * generic filler, which is the difference between a usable creative brief and a
 * stock image.
 */
export function ConceptCard({
  concept,
  index,
  busy,
  canWrite,
  onCreateVariations,
  onRegenerate,
  onArchive,
}: {
  concept: CreativeConcept;
  index: number;
  busy: boolean;
  canWrite: boolean;
  onCreateVariations: (conceptId: string, axes: VariationAxis[]) => void;
  onRegenerate: (conceptId: string, kind: "image" | "video") => void;
  onArchive: (conceptId: string, archived: boolean) => void;
}) {
  const [axis, setAxis] = useState<"" | VariationAxis>("");
  const [showPrompts, setShowPrompts] = useState(false);
  const archived = Boolean(concept.archived_at);
  const visual = concept.visual_direction || {};

  return (
    <Card className={archived ? "opacity-60" : undefined}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Badge tone="accent">Concept {String.fromCharCode(65 + index)}</Badge>
            {archived ? <Badge>Archived</Badge> : null}
            {concept.data_source === "demo" ? <Badge tone="demo">Demo</Badge> : null}
          </div>
          <h3 className="mt-2 font-display text-xl">{concept.angle}</h3>
        </div>
        <div className="text-right text-xs text-[var(--muted)]">
          {concept.platform ? <div>{concept.platform}</div> : null}
          {concept.tone ? <div>{concept.tone}</div> : null}
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <Field label="Hook" value={concept.hook} emphasis />
        <Field label="Headline" value={concept.headline} />
        <Field label="Primary text" value={concept.primary_text} />
        {concept.description ? <Field label="Description" value={concept.description} /> : null}
        <div className="flex flex-wrap gap-4 text-sm">
          {concept.cta ? (
            <span>
              <span className="text-[var(--muted)]">CTA: </span>
              <span className="font-medium">{concept.cta}</span>
            </span>
          ) : null}
          {concept.audience ? (
            <span>
              <span className="text-[var(--muted)]">Audience: </span>
              {concept.audience}
            </span>
          ) : null}
        </div>
      </div>

      <div className="mt-5 rounded-xl bg-[var(--surface-2)] p-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Visual direction
        </div>
        <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
          <Detail term="Composition" value={visual.composition} />
          <Detail term="Subject" value={visual.subject} />
          <Detail term="Environment" value={visual.environment} />
          <Detail term="Lighting" value={visual.lighting} />
          <Detail term="Style" value={visual.style} />
          <Detail term="Text overlay" value={visual.text_overlay} />
        </dl>
        {visual.brand_elements?.length ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {visual.brand_elements.map((element) => (
              <Badge key={element}>{element}</Badge>
            ))}
          </div>
        ) : null}
        {concept.aspect_ratios.length ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
            Formats:
            {concept.aspect_ratios.map((ratio) => (
              <Badge key={ratio}>{ratio}</Badge>
            ))}
          </div>
        ) : null}

        <button
          type="button"
          className="mt-3 text-xs text-[var(--accent-ink)] underline"
          onClick={() => setShowPrompts((value) => !value)}
        >
          {showPrompts ? "Hide generation prompts" : "Show generation prompts"}
        </button>
        {showPrompts ? (
          <div className="mt-3 space-y-2 text-xs">
            {concept.image_prompt ? (
              <Prompt label="Image prompt" value={concept.image_prompt} />
            ) : null}
            {concept.video_prompt ? (
              <Prompt label="Video prompt" value={concept.video_prompt} />
            ) : null}
            {concept.negative_constraints.length ? (
              <Prompt label="Negative constraints" value={concept.negative_constraints.join(", ")} />
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-5">
        <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Generated media
        </div>
        {concept.assets.length ? (
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            {concept.assets.map((asset, position) => (
              <AssetTile key={asset.id || asset.job_id || `${position}`} asset={asset} />
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-[var(--muted)]">
            No media was requested for this concept.
          </p>
        )}
      </div>

      {concept.variations.length ? (
        <div className="mt-5">
          <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Variations ({concept.variations.length})
          </div>
          <div className="mt-2 space-y-3">
            {concept.variations.map((variation) => (
              <div key={variation.id} className="rounded-xl border border-[var(--line)] p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="accent">{axisLabel(variation.axis)}</Badge>
                  <Badge>{variation.creative_type}</Badge>
                  <span className="text-xs text-[var(--muted)]">{variation.reference}</span>
                </div>
                <p className="mt-2 text-sm">
                  <span className="text-[var(--muted)]">Hypothesis: </span>
                  {variation.hypothesis}
                </p>
                {variation.hook ? <Field label="Hook" value={variation.hook} emphasis /> : null}
                {variation.headline ? <Field label="Headline" value={variation.headline} /> : null}
                {variation.primary_text ? (
                  <Field label="Primary text" value={variation.primary_text} />
                ) : null}
                {variation.cta ? (
                  <p className="mt-1 text-sm">
                    <span className="text-[var(--muted)]">CTA: </span>
                    {variation.cta}
                  </p>
                ) : null}
                {variation.assets.length ? (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    {variation.assets.map((asset, position) => (
                      <AssetTile key={asset.id || asset.job_id || `${position}`} asset={asset} />
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {canWrite ? (
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-[var(--line)] pt-4">
          <Select
            className="w-44"
            value={axis}
            disabled={busy}
            onChange={(event) => setAxis(event.target.value as "" | VariationAxis)}
          >
            <option value="">Any axis</option>
            {AXES.map((option) => (
              <option key={option} value={option}>
                {axisLabel(option)}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => onCreateVariations(concept.id, axis ? [axis] : [])}
          >
            Create variations
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onRegenerate(concept.id, "image")}
          >
            Regenerate image
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onRegenerate(concept.id, "video")}
          >
            Regenerate video
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onArchive(concept.id, !archived)}
          >
            {archived ? "Restore" : "Archive"}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function Field({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string | null | undefined;
  emphasis?: boolean;
}) {
  if (!value) return null;
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <p className={emphasis ? "mt-1 font-medium" : "mt-1 text-sm whitespace-pre-line"}>{value}</p>
    </div>
  );
}

function Detail({ term, value }: { term: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs text-[var(--muted)]">{term}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Prompt({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <p className="mt-1 rounded-lg bg-[var(--surface)] p-2 font-mono text-[11px] leading-relaxed">
        {value}
      </p>
    </div>
  );
}
