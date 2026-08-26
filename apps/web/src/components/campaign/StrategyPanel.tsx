"use client";

import { DataLimitations } from "@/components/campaign/DataLimitations";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import type { CampaignStrategy } from "@/types/campaign-generation";

const SECTIONS: { key: keyof CampaignStrategy; label: string }[] = [
  { key: "current_situation", label: "Current situation" },
  { key: "problem", label: "Problem" },
  { key: "opportunity", label: "Opportunity" },
  { key: "target_audience", label: "Target audience" },
  { key: "positioning", label: "Positioning" },
  { key: "core_message", label: "Core message" },
  { key: "offer_strategy", label: "Offer strategy" },
  { key: "creative_strategy", label: "Creative strategy" },
  { key: "channel_strategy", label: "Channel strategy" },
  { key: "campaign_objective", label: "Campaign objective" },
];

/**
 * The strategy document, with its evidence kept attached.
 *
 * Evidence is rendered as claim plus source so a reader can tell a finding
 * ("47 leads in the last 30 days") from a judgement (an unsourced
 * recommendation). Anything the agent could not support appears under data
 * limitations instead of being dressed up as a number.
 */
export function StrategyPanel({ strategy }: { strategy: CampaignStrategy }) {
  return (
    <Card>
      <CardHeader
        title="Campaign strategy"
        subtitle="Generated from this client's stored context. Claims that rest on analytics carry their source."
      />

      <div className="grid gap-4 md:grid-cols-2">
        {SECTIONS.map((section) => {
          const value = strategy[section.key];
          if (typeof value !== "string" || !value) return null;
          return (
            <div key={section.key} className="rounded-xl border border-[var(--line)] p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                {section.label}
              </div>
              <p className="mt-2 text-sm leading-relaxed whitespace-pre-line">{value}</p>
            </div>
          );
        })}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <ListBlock title="Success metrics" items={strategy.success_metrics} />
        <ListBlock title="Risks" items={strategy.risks} tone="warning" />
      </div>

      {strategy.evidence.length ? (
        <div className="mt-4 rounded-xl border border-[var(--line)] p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Evidence
          </div>
          <ul className="mt-2 space-y-2 text-sm">
            {strategy.evidence.map((item, index) => (
              <li key={`${index}-${item.claim}`} className="flex flex-wrap items-baseline gap-2">
                <span>{item.claim}</span>
                <Badge tone="low">{item.source}</Badge>
                {item.value ? (
                  <span className="text-[var(--accent-ink)]">{item.value}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4">
        <DataLimitations items={strategy.data_limitations} />
      </div>
    </Card>
  );
}

function ListBlock({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone?: string;
}) {
  if (!items.length) return null;
  return (
    <div className="rounded-xl border border-[var(--line)] p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
        {title}
      </div>
      <ul className="mt-2 space-y-1 text-sm">
        {items.map((item, index) => (
          <li key={`${index}-${item}`} className="flex items-start gap-2">
            <Badge tone={tone || "low"} className="mt-0.5">
              {index + 1}
            </Badge>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
