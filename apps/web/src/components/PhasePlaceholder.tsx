import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";

export function PhasePlaceholder({
  title,
  phase,
  description,
}: {
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">{title}</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">{description}</p>
      </div>
      <Card>
        <CardHeader title="Not implemented in Phase 1" action={<Badge tone="demo">{phase}</Badge>} />
        <p className="text-sm text-[var(--muted)]">
          Architecture hooks exist. This screen will light up when the corresponding phase is delivered.
          We do not claim live integrations or fabricated analytics here.
        </p>
      </Card>
    </div>
  );
}
