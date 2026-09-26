"use client";

import { useMemo, useState } from "react";
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

type Finding = {
  id: string;
  rule_id: string;
  category: string;
  severity: string;
  title: string;
  description: string;
  url?: string | null;
  evidence: Record<string, unknown>;
  recommendation?: string | null;
};

type Summary = {
  total_findings: number;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  affected_pages: number;
  disclaimer: string;
};

function safeText(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

export default function SeoCrawlerPage() {
  const [rootUrl, setRootUrl] = useState("https://example.com");
  const [maxPages, setMaxPages] = useState(25);
  const [crawl, setCrawl] = useState<Crawl | null>(null);
  const [pages, setPages] = useState<CrawlPage[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [severityFilter, setSeverityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"pages" | "findings">("findings");

  const categories = useMemo(
    () => Array.from(new Set(findings.map((f) => f.category))).sort(),
    [findings]
  );

  async function loadAnalysis(crawlId: string) {
    const summaryData = await api<Summary>(`/seo/crawls/${crawlId}/summary`);
    setSummary(summaryData);
    const params = new URLSearchParams({ limit: "100" });
    if (severityFilter) params.set("severity", severityFilter);
    if (categoryFilter) params.set("category", categoryFilter);
    const findingRows = await api<Finding[]>(`/seo/crawls/${crawlId}/findings?${params.toString()}`);
    setFindings(findingRows);
  }

  async function startCrawl() {
    setBusy(true);
    setError(null);
    setPages([]);
    setFindings([]);
    setSummary(null);
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
        await loadAnalysis(created.id);
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

  async function applyFilters() {
    if (!crawl) return;
    setBusy(true);
    try {
      await loadAnalysis(crawl.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load findings");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="SEO Site Crawler" subtitle="Read-only crawl and technical analysis — never modifies your site." />
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
            Pages crawled: {String(crawl.stats?.pages_crawled ?? 0)} · Findings:{" "}
            {String(crawl.stats?.findings_count ?? summary?.total_findings ?? 0)}
          </p>
          {crawl.error ? <p className="text-sm text-red-600">{safeText(crawl.error)}</p> : null}
        </Card>
      ) : null}

      {summary ? (
        <Card>
          <CardHeader title="Technical SEO summary" subtitle={`${summary.total_findings} findings · ${summary.affected_pages} pages affected`} />
          <div className="grid gap-2 text-sm md:grid-cols-2">
            <div>
              <p className="font-medium">By severity</p>
              {Object.entries(summary.by_severity).map(([sev, count]) => (
                <p key={sev} className="text-[var(--muted)]">
                  {sev}: {count}
                </p>
              ))}
            </div>
            <div>
              <p className="font-medium">By category</p>
              {Object.entries(summary.by_category).map(([cat, count]) => (
                <p key={cat} className="text-[var(--muted)]">
                  {cat}: {count}
                </p>
              ))}
            </div>
          </div>
          <p className="mt-3 text-xs text-[var(--muted)]">{summary.disclaimer}</p>
        </Card>
      ) : null}

      {crawl && (pages.length || findings.length) ? (
        <Card>
          <div className="mb-4 flex flex-wrap gap-2">
            <Button variant={view === "findings" ? "primary" : "secondary"} onClick={() => setView("findings")}>
              Findings
            </Button>
            <Button variant={view === "pages" ? "primary" : "secondary"} onClick={() => setView("pages")}>
              Pages
            </Button>
          </div>

          {view === "findings" ? (
            <>
              <div className="mb-4 grid gap-2 md:grid-cols-3">
                <select
                  className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
                  value={severityFilter}
                  onChange={(e) => setSeverityFilter(e.target.value)}
                >
                  <option value="">All severities</option>
                  {["INFO", "LOW", "MEDIUM", "HIGH"].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <select
                  className="h-10 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm"
                  value={categoryFilter}
                  onChange={(e) => setCategoryFilter(e.target.value)}
                >
                  <option value="">All categories</option>
                  {categories.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <Button variant="secondary" disabled={busy} onClick={applyFilters}>
                  Apply filters
                </Button>
              </div>
              {findings.length === 0 ? (
                <p className="text-sm text-[var(--muted)]">No findings match the current filters.</p>
              ) : (
                <div className="space-y-2">
                  {findings.map((finding) => (
                    <div key={finding.id} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{safeText(finding.title)}</span>
                        <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(finding.severity)}</span>
                        <span className="rounded bg-[var(--surface)] px-2 py-0.5 text-xs">{safeText(finding.category)}</span>
                      </div>
                      {finding.url ? <div className="mt-1 text-[var(--muted)]">{safeText(finding.url)}</div> : null}
                      <p className="mt-1">{safeText(finding.description)}</p>
                      {finding.recommendation ? (
                        <p className="mt-1 text-[var(--muted)]">Recommendation: {safeText(finding.recommendation)}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="space-y-2">
              {pages.map((page) => (
                <div key={page.url} className="rounded-lg border border-[var(--line)] p-3 text-sm">
                  <div className="font-medium">{safeText(page.url)}</div>
                  <div className="text-[var(--muted)]">
                    depth {page.depth} · HTTP {page.http_status ?? "—"}
                    {page.error_code ? ` · ${safeText(page.error_code)}` : ""}
                  </div>
                  {page.observations?.title ? <div>Title: {safeText(page.observations.title)}</div> : null}
                </div>
              ))}
            </div>
          )}
        </Card>
      ) : null}
    </div>
  );
}
