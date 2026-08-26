"use client";

import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { stageGlyph, stageProgressLabel, stageTone } from "@/lib/campaign-generation";
import type { CampaignGenerationRun } from "@/types/campaign-generation";

/**
 * The live checklist for a generation run.
 *
 * Every row renders exactly what the worker recorded. There is no progress bar
 * spanning the whole run because the total is not knowable up front — a media
 * stage can turn out to be NOT_CONFIGURED — and a bar that reached 90% and then
 * stopped would be a lie told smoothly.
 */
export function GenerationProgress({
  run,
  title = "Generating campaign",
}: {
  run: CampaignGenerationRun;
  title?: string;
}) {
  const finished = run.status === "READY_FOR_REVIEW";
  const failed = run.status === "FAILED";

  return (
    <Card>
      <CardHeader
        title={title}
        subtitle={
          finished
            ? "Campaign ready for review."
            : failed
              ? "Generation stopped before finishing."
              : "Working. This page updates from real job status."
        }
        action={<Badge tone={failed ? "danger" : finished ? "success" : "accent"}>{run.status.replaceAll("_", " ")}</Badge>}
      />

      <ul className="space-y-2">
        {run.stages.map((stage) => {
          const progress = stageProgressLabel(stage);
          const running = stage.status.toUpperCase() === "RUNNING";
          return (
            <li
              key={stage.key}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--line)] px-4 py-3"
            >
              <span
                className={`w-4 text-center text-sm ${running ? "animate-pulseSoft" : ""}`}
                aria-hidden
              >
                {stageGlyph(stage.status)}
              </span>
              <span className="text-sm font-medium">{stage.label}</span>
              {progress ? (
                <span className="text-sm text-[var(--muted)]">{progress}</span>
              ) : null}
              <Badge tone={stageTone(stage.status)} className="ml-auto">
                {stage.status.replaceAll("_", " ")}
              </Badge>
              {stage.detail ? (
                <p className="w-full text-xs text-[var(--muted)]">{stage.detail}</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      {run.error ? (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {run.error}
          {run.error_code ? (
            <span className="ml-2 text-xs uppercase tracking-wide">{run.error_code}</span>
          ) : null}
        </div>
      ) : null}

      {run.demo_mode ? (
        <p className="mt-4 text-xs text-[var(--muted)]">
          This organization is in demo mode. Assets are labelled DEMO and must not be treated as
          production creative.
        </p>
      ) : null}
    </Card>
  );
}
