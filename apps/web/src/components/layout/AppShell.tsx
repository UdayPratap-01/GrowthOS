"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, clearTokens } from "@/lib/api";
import { User } from "@/types";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { Skeleton } from "@/components/ui/Skeleton";

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const me = await api<User>("/auth/me");
        if (mounted) setUser(me);
      } catch {
        clearTokens();
        router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [pathname, router]);

  if (loading) {
    return (
      <div className="flex min-h-screen bg-[var(--canvas)]">
        <div className="w-[260px] border-r border-[var(--line)] p-4">
          <Skeleton className="mb-8 h-10 w-40" />
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="mb-3 h-9 w-full" />
          ))}
        </div>
        <div className="flex-1 p-8">
          <Skeleton className="mb-6 h-10 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-[var(--canvas)]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar user={user} />
        <main className="flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
