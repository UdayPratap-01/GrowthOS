"""Safe outbound HTTP fetcher with SSRF checks on every redirect hop."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.seo.ssrf import SsrfError, validate_url_target


@dataclass
class FetchResult:
    requested_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    body: bytes
    response_bytes: int
    redirect_count: int
    response_time_ms: float | None
    error_code: str | None = None
    error_message: str | None = None


class SafeFetcher:
    USER_AGENT = "GrowthOS-SEO-Crawler/1.0 (read-only)"

    def __init__(
        self,
        *,
        timeout: float,
        max_response_bytes: int,
        max_redirects: int,
        validate=validate_url_target,
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self._validate = validate

    async def fetch(self, url: str) -> FetchResult:
        started = time.perf_counter()
        current = url
        redirect_count = 0
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                while True:
                    await self._validate(current)
                    resp = await client.get(current, headers={"User-Agent": self.USER_AGENT})
                    if resp.status_code in {301, 302, 303, 307, 308}:
                        location = resp.headers.get("location")
                        if not location:
                            return self._result(
                                url,
                                current,
                                resp.status_code,
                                resp.headers.get("content-type"),
                                b"",
                                redirect_count,
                                started,
                                "redirect_missing_location",
                                "Redirect response missing Location header",
                            )
                        redirect_count += 1
                        if redirect_count > self.max_redirects:
                            return self._result(
                                url,
                                current,
                                resp.status_code,
                                resp.headers.get("content-type"),
                                b"",
                                redirect_count,
                                started,
                                "too_many_redirects",
                                "Maximum redirects exceeded",
                            )
                        current = str(httpx.URL(current).join(location))
                        continue
                    body = await self._read_limited(resp)
                    return self._result(
                        url,
                        str(resp.url),
                        resp.status_code,
                        resp.headers.get("content-type"),
                        body,
                        redirect_count,
                        started,
                    )
        except SsrfError as exc:
            return self._result(url, None, None, None, b"", redirect_count, started, exc.code, exc.message)
        except httpx.TimeoutException:
            return self._result(url, None, None, None, b"", redirect_count, started, "timeout", "Request timed out")
        except httpx.HTTPError as exc:
            return self._result(url, None, None, None, b"", redirect_count, started, "http_error", type(exc).__name__)

    async def head(self, url: str) -> FetchResult:
        started = time.perf_counter()
        try:
            await self._validate(url)
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                resp = await client.head(url, headers={"User-Agent": self.USER_AGENT})
                return self._result(url, str(resp.url), resp.status_code, resp.headers.get("content-type"), b"", 0, started)
        except SsrfError as exc:
            return self._result(url, None, None, None, b"", 0, started, exc.code, exc.message)
        except httpx.TimeoutException:
            return self._result(url, None, None, None, b"", 0, started, "timeout", "Request timed out")
        except httpx.HTTPError as exc:
            return self._result(url, None, None, None, b"", 0, started, "http_error", type(exc).__name__)

    async def _read_limited(self, resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise httpx.HTTPError("response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _result(
        self,
        requested: str,
        final: str | None,
        status: int | None,
        content_type: str | None,
        body: bytes,
        redirects: int,
        started: float,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> FetchResult:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return FetchResult(
            requested_url=requested,
            final_url=final,
            status_code=status,
            content_type=(content_type or "").split(";", 1)[0].strip().lower() or None,
            body=body,
            response_bytes=len(body),
            redirect_count=redirects,
            response_time_ms=elapsed_ms,
            error_code=error_code,
            error_message=error_message,
        )
