"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { api } from "@/lib/api";

type Competitor = {
  id: string;
  display_name?: string | null;
  root_url: string;
  domain: string;
  status: string;
};

type Crawl = {
  id: string;
  status: string;
  stats: Record<string, unknown>;
  error?: string | null;
};

function safeText(v: unknown): string {
  if (v == null) return "";
  return String(v);
}

export default function SeoCompetitorsPage() {
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [crawlStatus, setCrawlStatus] = useState<Record<string, Crawl>>({});

  const load = useCallback(async () => {
    setError(null);
    try {
      const rows = await api<Competitor[]>("/seo/competitors");
      setCompetitors(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load competitors");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function addCompetitor() {
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api("/seo/competitors", {
        method: "POST",
        body: JSON.stringify({ root_url: url.trim(), display_name: name.trim() || undefined }),
      });
      setUrl("");
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add competitor");
    } finally {
      setBusy(false);
    }
  }

  async function removeCompetitor(id: string) {
    setBusy(true);
    try {
      await api(`/seo/competitors/${id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove competitor");
    } finally {
      setBusy(false);
    }
  }

  async function startCrawl(id: string) {
    setBusy(true);
    setError(null);
    try {
      const crawl = await api<Crawl>(`/seo/competitors/${id}/crawl`, {
        method: "POST",
        body: JSON.stringify({ max_pages: 25 }),
      });
      setCrawlStatus((s) => ({ ...s, [id]: crawl }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Crawl failed to start");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="SEO Competitors"
          subtitle="Add competitor domains for bounded read-only crawling. No ranking or traffic data is inferred."
        />
        <div className="grid gap-3 md:grid-cols-2">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://competitor.example" />
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name (optional)" />
        </div>
        <Button className="mt-3" disabled={busy} onClick={() => void addCompetitor()}>
          Add competitor
        </Button>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      <Card>
        {competitors.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No competitors configured. Add a competitor URL to enable content-gap analysis.</p>
        ) : (
          <div className="space-y-2">
            {competitors.map((c) => (
              <div key={c.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="font-medium">{safeText(c.display_name || c.domain)}</div>
                <div className="text-[var(--muted)]">{safeText(c.root_url)}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button disabled={busy} onClick={() => void startCrawl(c.id)}>
                    Start crawl
                  </Button>
                  <Button disabled={busy} onClick={() => void removeCompetitor(c.id)}>
                    Remove
                  </Button>
                </div>
                {crawlStatus[c.id] ? (
                  <div className="mt-2 text-[var(--muted)]">
                    Crawl status: {safeText(crawlStatus[c.id].status)}
                    {crawlStatus[c.id].error ? ` · ${safeText(crawlStatus[c.id].error)}` : ""}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
