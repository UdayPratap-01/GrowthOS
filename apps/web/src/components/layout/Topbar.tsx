"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { logout } from "@/lib/api";
import { User } from "@/types";
import { useRouter } from "next/navigation";

export function Topbar({ user }: { user: User | null }) {
  const router = useRouter();
  const mode = user?.operating_mode || (user?.demo_mode ? "DEMO" : "LIVE");

  return (
    <header className="border-b border-[var(--line)] bg-[var(--surface)]/80 backdrop-blur">
      <div
        className={
          mode === "DEMO"
            ? "bg-sky-500/15 px-6 py-1.5 text-center text-xs font-medium text-sky-900"
            : "bg-emerald-500/15 px-6 py-1.5 text-center text-xs font-medium text-emerald-900"
        }
      >
        {mode === "DEMO"
          ? "DEMO MODE — KPIs may include seed data; simulated executions are labeled DEMO DATA and are not live platform confirmations."
          : "LIVE MODE — metrics from the database; external success only after official API confirmation. Seed rows (if any) show as mixed."}
      </div>
      <div className="flex items-center justify-between px-6 py-4">
        <div>
          <div className="text-sm text-[var(--muted)]">Organization</div>
          <div className="font-medium text-[var(--ink)]">{user?.organization_name || "—"}</div>
        </div>
        <div className="flex items-center gap-3">
          <Badge tone={mode === "DEMO" ? "demo" : "success"}>{mode === "DEMO" ? "Demo mode" : "Live mode"}</Badge>
          {user?.env_demo_mode && !user.demo_mode ? (
            <Badge tone="warning">Env DEMO_MODE still on</Badge>
          ) : null}
          <div className="text-right">
            <div className="text-sm font-medium text-[var(--ink)]">{user?.full_name}</div>
            <div className="text-xs text-[var(--muted)]">{user?.email}</div>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={async () => {
              await logout();
              router.push("/login");
            }}
          >
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
