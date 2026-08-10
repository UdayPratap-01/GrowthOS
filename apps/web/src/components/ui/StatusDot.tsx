import { cn } from "@/lib/utils";

const colors: Record<string, string> = {
  // Integrations
  not_connected: "bg-slate-400",
  connecting: "bg-amber-500",
  connected: "bg-emerald-500",
  sync_error: "bg-rose-500",
  disconnected: "bg-slate-500",
  demo_data: "bg-sky-500",
  // Campaign / action lifecycle
  draft: "bg-slate-400",
  pending: "bg-amber-500",
  pending_approval: "bg-amber-500",
  approved: "bg-emerald-500",
  executing: "bg-sky-500",
  published: "bg-emerald-600",
  completed: "bg-emerald-600",
  failed: "bg-rose-500",
  rejected: "bg-rose-500",
  cancelled: "bg-slate-500",
};

export function StatusDot({ status, label }: { status: string; label?: string }) {
  const key = status.toLowerCase();
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--muted)]">
      <span className={cn("h-2 w-2 rounded-full", colors[key] || "bg-slate-400")} />
      {label || status.replaceAll("_", " ")}
    </span>
  );
}
