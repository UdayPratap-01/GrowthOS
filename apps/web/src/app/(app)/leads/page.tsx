"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { api } from "@/lib/api";
import { Client } from "@/types";

export default function LeadsPage() {
  const router = useRouter();
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const list = await api<Client[]>("/clients");
      setClients(list);
      if (list[0]) setClientId(list[0].id);
      setLoading(false);
    })();
  }, []);

  if (loading) return <Skeleton className="h-48 w-full" />;
  if (!clients.length) return <EmptyState title="No clients" description="Leads are client-scoped for tenant isolation." />;

  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">Leads</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">CRM with AI scoring, kanban, and table views.</p>
      </div>
      <Card>
        <CardHeader title="Open client CRM" />
        <div className="flex flex-wrap gap-3">
          <Select className="max-w-sm" value={clientId} onChange={(e) => setClientId(e.target.value)}>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.business_name}</option>)}
          </Select>
          <Button onClick={() => router.push(`/clients/${clientId}?tab=leads`)}>Open leads</Button>
        </div>
      </Card>
    </div>
  );
}
