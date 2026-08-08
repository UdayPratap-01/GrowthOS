import { cn } from "@/lib/utils";

const tones: Record<string, string> = {
  default: "bg-[var(--surface-2)] text-[var(--muted)]",
  accent: "bg-[var(--accent-soft)] text-[var(--accent-ink)]",
  success: "bg-emerald-500/15 text-emerald-700",
  warning: "bg-amber-500/15 text-amber-800",
  danger: "bg-rose-500/15 text-rose-700",
  demo: "bg-sky-500/15 text-sky-800",
  high: "bg-rose-500/15 text-rose-700",
  critical: "bg-rose-600/20 text-rose-800",
  medium: "bg-amber-500/15 text-amber-800",
  low: "bg-slate-500/15 text-slate-700",
};

export function Badge({
  children,
  tone = "default",
  className,
}: {
  children: React.ReactNode;
  tone?: keyof typeof tones | string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
        tones[tone] || tones.default,
        className
      )}
    >
      {children}
    </span>
  );
}
