"use client";

import { useEffect, useState } from "react";
import { fetchMediaObjectUrl } from "@/lib/api";

type Props = {
  url: string | null | undefined;
  mimeType?: string | null;
  alt?: string;
  demo?: boolean;
};

export function MediaPreview({ url, mimeType, alt, demo }: Props) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    if (!url) return;
    (async () => {
      try {
        const obj = await fetchMediaObjectUrl(url);
        if (cancelled) {
          URL.revokeObjectURL(obj);
          return;
        }
        revoked = obj;
        setObjectUrl(obj);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load media");
      }
    })();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [url]);

  if (!url) {
    return <div className="flex h-40 items-center justify-center rounded-xl bg-[var(--surface-2)] text-xs text-[var(--muted)]">No media file</div>;
  }
  if (error) {
    return <div className="flex h-40 items-center justify-center rounded-xl border border-red-200 bg-red-50 text-xs text-red-700">{error}</div>;
  }
  if (!objectUrl) {
    return <div className="flex h-40 items-center justify-center rounded-xl bg-[var(--surface-2)] text-xs text-[var(--muted)]">Loading…</div>;
  }

  const isVideo = (mimeType || "").startsWith("video/") || url.includes("video");
  return (
    <div className="relative overflow-hidden rounded-xl bg-black/5">
      {demo ? (
        <span className="absolute left-2 top-2 z-10 rounded bg-sky-600 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
          DEMO
        </span>
      ) : null}
      {isVideo ? (
        <video src={objectUrl} controls className="h-48 w-full object-contain" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={objectUrl} alt={alt || "Creative"} className="h-48 w-full object-cover" />
      )}
    </div>
  );
}
