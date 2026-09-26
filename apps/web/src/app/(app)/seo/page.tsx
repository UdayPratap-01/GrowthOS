"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { api } from "@/lib/api";
import { pollBackgroundJob } from "@/lib/jobs";

type Crawl = {
  id: string;
  root_url: string;
  status: string;
  stats: Record<string, unknown>;
  error?: string | null;
};

type CrawlPage = {
  url: string;
  http_status?: number | null;
  depth: number;
  error_code?: string | null;
  observations: Record<string, unknown>;
};

export default function SeoCrawlerPage() {
  const [rootUrl, setRootUrl] = useState("https://example.com");
  const [maxPages, setMaxPages] = useState(25);
  const [crawl, setCrawl] = useState<Crawl | null>(null);
  const [pages, setPages] = useState<CrawlPage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startCrawl() {
    setBusy(true);
    setError(null);
    setPages([]);
    try {
      const created = await api<Crawl>("/seo/crawls", {
        method: "POST",
        body: JSON.stringify({ root_url: rootUrl, max_pages: maxPages, max_depth: 3 }),
      });
      setCrawl(created);
      if (created.id) {
        const jobId = (created as Crawl & { job_id?: string }).job_id;
        if (jobId) {
          await pollBackgroundJob(jobId, { timeoutMs: 5 * 60 * 1000 });
        }
        const refreshed = await api<Crawl>(`/seo/crawls/${created.id}`);
        setCrawl(refreshed);
        const pageRows = await api<CrawlPage[]>(`/seo/crawls/${created.id}/pages?limit=100`);
        setPages(pageRows);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Crawl failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelCrawl() {
    if (!crawl) return;
    setBusy(true);
    try {
      const updated = await api<Crawl>(`/seo/crawls/${crawl.id}/cancel`, { method: "POST" });
      setCrawl(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="SEO Site Crawler" subtitle="Read-only crawl — observations only, never modifies your site." />
        <div className="grid gap-3 md:grid-cols-3">
          <div className="space-y-1">
            <label className="text-xs text-[var(--muted)]">Root URL</label>
            <Input value={rootUrl} onChange={(e) => setRootUrl(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-[var(--muted)]">Max pages</label>
            <Input
              type="number"
              value={String(maxPages)}
              onChange={(e) => setMaxPages(Number(e.target.value) || 25)}
            />
          </div>
          <div className="flex items-end gap-2">
            <Button disabled={busy} onClick={startCrawl}>
              {busy ? "Running…" : "Start crawl"}
            </Button>
            {crawl && crawl.status === "running" ? (
              <Button variant="secondary" disabled={busy} onClick={cancelCrawl}>
                Cancel
              </Button>
            ) : null}
          </div>
        </div>
        {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      </Card>

      {crawl ? (
        <Card>
          <CardHeader title="Crawl status" subtitle={crawl.root_url} />
          <p className="text-sm">Status: {crawl.status}</p>
          <p className="text-sm text-[var(--muted)]">
            Pages crawled: {String(crawl.stats?.pages_crawled ?? 0)} · Discovered:{" "}
            {String(crawl.stats?.pages_discovered ?? 0)}
          </p>
          {crawl.error ? <p className="text-sm text-red-600">{crawl.error}</p> : null}
        </Card>
      ) : null}

      {pages.length ? (
        <Card>
          <CardHeader title="Pages" subtitle={`${pages.length} results`} />
          <div className="space-y-2">
            {pages.map((page) => (
              <div key={page.url} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                <div className="font-medium">{page.url}</div>
                <div className="text-[var(--muted)]">
                  depth {page.depth} · HTTP {page.http_status ?? "—"}
                  {page.error_code ? ` · ${page.error_code}` : ""}
                </div>
                {page.observations?.title ? <div>Title: {String(page.observations.title)}</div> : null}
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
