"""Read-only Google Search Console API client."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

GSC_API = "https://www.googleapis.com/webmasters/v3"
DEFAULT_ROW_LIMIT = 250
MAX_ROW_LIMIT = 500


class SearchConsoleClient:
    def __init__(self, *, access_token: str, timeout: float = 45.0) -> None:
        self.access_token = access_token
        self.timeout = timeout

    async def list_sites(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{GSC_API}/sites",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Search Console sites list failed: HTTP {resp.status_code}")
        return [
            {"siteUrl": entry.get("siteUrl"), "permissionLevel": entry.get("permissionLevel")}
            for entry in (resp.json().get("siteEntry") or [])
            if entry.get("siteUrl")
        ]

    async def query_search_analytics(
        self,
        site_url: str,
        *,
        start_date: str,
        end_date: str,
        dimensions: list[str],
        row_limit: int = DEFAULT_ROW_LIMIT,
        start_row: int = 0,
    ) -> list[dict[str, Any]]:
        limit = min(max(row_limit, 1), MAX_ROW_LIMIT)
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": limit,
            "startRow": start_row,
        }
        encoded_site = quote(site_url, safe="")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{GSC_API}/sites/{encoded_site}/searchAnalytics/query",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Search analytics query failed: HTTP {resp.status_code}")
        rows: list[dict[str, Any]] = []
        for row in resp.json().get("rows") or []:
            keys = row.get("keys") or []
            mapped: dict[str, Any] = {
                "clicks": float(row.get("clicks") or 0),
                "impressions": float(row.get("impressions") or 0),
                "ctr": float(row.get("ctr") or 0),
                "position": float(row.get("position") or 0),
            }
            for idx, dim in enumerate(dimensions):
                mapped[dim] = keys[idx] if idx < len(keys) else None
            rows.append(mapped)
        return rows

    async def fetch_all_rows(
        self,
        site_url: str,
        *,
        start_date: str,
        end_date: str,
        dimensions: list[str],
        max_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        start_row = 0
        while len(collected) < max_rows:
            batch = await self.query_search_analytics(
                site_url,
                start_date=start_date,
                end_date=end_date,
                dimensions=dimensions,
                row_limit=min(DEFAULT_ROW_LIMIT, max_rows - len(collected)),
                start_row=start_row,
            )
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < DEFAULT_ROW_LIMIT:
                break
            start_row += len(batch)
        return collected[:max_rows]
