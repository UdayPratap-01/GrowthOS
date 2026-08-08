import { Button } from "./Button";

export function EmptyState({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--line)] bg-[var(--surface)] px-6 py-16 text-center">
      <h3 className="font-display text-xl text-[var(--ink)]">{title}</h3>
      <p className="mt-2 max-w-md text-sm text-[var(--muted)]">{description}</p>
      {actionLabel && onAction ? (
        <Button className="mt-5" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
