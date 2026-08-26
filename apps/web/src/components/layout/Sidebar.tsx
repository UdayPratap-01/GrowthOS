"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Bot,
  Briefcase,
  CheckSquare,
  FileText,
  LayoutDashboard,
  Megaphone,
  Plug,
  Settings,
  Sparkles,
  Gauge,
  Target,
  Users,
  Waypoints,
  Rocket,
  Images,
  Wand2,
  Clapperboard,
} from "lucide-react";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/clients", label: "Clients", icon: Briefcase },
  { href: "/ai-strategy", label: "AI Strategy", icon: Sparkles },
  { href: "/content-studio", label: "Content Studio", icon: Megaphone },
  { href: "/campaigns", label: "Campaigns", icon: Target },
  { href: "/campaign-builder", label: "Campaign Builder", icon: Wand2 },
  { href: "/ai-campaigns", label: "AI Campaigns", icon: Clapperboard },
  { href: "/creative-library", label: "Creative Library", icon: Images },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/lead-scoring", label: "Lead Scoring", icon: Gauge },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/recommendations", label: "Recommendations", icon: Sparkles },
  { href: "/competitors", label: "Competitors", icon: Waypoints },
  { href: "/autopilot", label: "Autopilot", icon: Rocket },
  { href: "/approvals", label: "Approvals", icon: CheckSquare },
  { href: "/ai-activity", label: "AI Activity", icon: Activity },
  { href: "/ai-assistant", label: "AI Assistant", icon: Bot },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 flex h-screen w-[260px] shrink-0 flex-col bg-[var(--panel)] px-4 py-5 text-[var(--panel-text)]">
      <div className="mb-8 px-2">
        <div className="font-display text-2xl text-white">GrowthOS</div>
        <div className="mt-1 text-[11px] uppercase tracking-[0.2em] text-[var(--accent)]">AI Marketing OS</div>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto">
        {nav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition",
                active
                  ? "bg-[var(--accent)] font-medium text-[#04241f]"
                  : "text-[var(--panel-muted)] hover:bg-white/5 hover:text-white"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-3 text-xs text-[var(--panel-muted)]">
        Phase 5 Autopilot
        <div className="mt-1 text-[var(--panel-text)]">Approvals · structured actions · safety limits.</div>
      </div>
    </aside>
  );
}
