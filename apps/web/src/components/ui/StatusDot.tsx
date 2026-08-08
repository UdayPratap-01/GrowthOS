import { cn } from "@/lib/utils";

const colors: Record<string, string> = {
  connected: "bg-emerald-500",
  not_connected: "bg-slate-400",
  demo_data: "bg-sky-500",
  sync_error: "bg-rose-500",
  pending: "bg-amber-500",
  approved: "bg-emerald-500",
  rejected: "bg-rose-500",
  completed: "bg-slate-500",
};

export function StatusDot({ status, label }: { status: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
      <span className={cn("h-2 w-2 rounded-full", colors[status] || "bg-slate-400")} />
      {label || status.replaceAll("_", " ")}
    </span>
  );
}
