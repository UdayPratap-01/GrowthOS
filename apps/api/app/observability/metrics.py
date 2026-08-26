"""
In-process metrics registry.

Provider-independent by design: counters and histograms live in memory and are
exposed in both JSON and Prometheus text format. Anything that scrapes an HTTP
endpoint — Prometheus, Grafana Agent, Datadog's OpenMetrics check — can consume
it without the application depending on a vendor SDK.

Every value comes from a real application event. Nothing here is synthesised,
and a metric that has never been observed is absent rather than reported as zero
activity.

Scope: counters are per-process and reset on restart. That is adequate for rate
and error-ratio alerting, which is what these are for; long-term aggregation is
the scraper's job.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

_LOCK = threading.Lock()


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0
    buckets: dict[float, int] = field(default_factory=dict)

    def observe(self, value: float, bounds: tuple[float, ...]) -> None:
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)
        for bound in bounds:
            if value <= bound:
                self.buckets[bound] = self.buckets.get(bound, 0) + 1


LATENCY_BUCKETS_MS = (10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0)

_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
_histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}
_started_at = time.time()


def _key(name: str, labels: dict[str, str] | None):
    return name, tuple(sorted((k, str(v)) for k, v in (labels or {}).items()))


def increment(name: str, *, labels: dict[str, str] | None = None, value: int = 1) -> None:
    with _LOCK:
        _counters[_key(name, labels)] += value


def observe(name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
    with _LOCK:
        key = _key(name, labels)
        hist = _histograms.get(key)
        if hist is None:
            hist = _Histogram()
            _histograms[key] = hist
        hist.observe(value, LATENCY_BUCKETS_MS)


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _counters.clear()
        _histograms.clear()


# --------------------------------------------------------------------------
# Recording helpers — one per event class the audit asked to see
# --------------------------------------------------------------------------


def record_request(*, method: str, path: str, status_code: int, duration_ms: float) -> None:
    labels = {"method": method, "path": path, "status": str(status_code)}
    increment("http_requests_total", labels=labels)
    observe("http_request_duration_ms", duration_ms, labels={"method": method, "path": path})
    if status_code >= 500:
        increment("http_server_errors_total", labels={"method": method, "path": path})
    elif status_code >= 400:
        increment("http_client_errors_total", labels={"method": method, "path": path})


def record_job(*, job_type: str, status: str) -> None:
    increment("jobs_total", labels={"job_type": job_type, "status": status.lower()})
    if status.lower() == "failed":
        increment("job_failures_total", labels={"job_type": job_type})


def record_ai(*, provider: str, operation: str, success: bool) -> None:
    increment("ai_requests_total", labels={"provider": provider, "operation": operation})
    if not success:
        increment("ai_failures_total", labels={"provider": provider, "operation": operation})


def record_media(*, kind: str, provider: str, status: str) -> None:
    increment("media_generations_total", labels={"kind": kind, "provider": provider, "status": status.lower()})
    if status.lower() == "failed":
        increment("media_failures_total", labels={"kind": kind, "provider": provider})


def record_storage(*, operation: str, success: bool) -> None:
    increment("storage_operations_total", labels={"operation": operation})
    if not success:
        increment("storage_failures_total", labels={"operation": operation})


def record_integration(*, provider: str, success: bool) -> None:
    increment("integration_syncs_total", labels={"provider": provider})
    if not success:
        increment("integration_failures_total", labels={"provider": provider})


def record_rate_limited(*, scope: str) -> None:
    increment("rate_limited_total", labels={"scope": scope})


def record_auth(*, outcome: str) -> None:
    increment("auth_attempts_total", labels={"outcome": outcome})


def record_database_error(*, kind: str) -> None:
    increment("database_errors_total", labels={"kind": kind})


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def snapshot() -> dict:
    """JSON view, used by the metrics endpoint and by tests."""
    with _LOCK:
        counters = [
            {"name": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(_counters.items())
        ]
        histograms = [
            {
                "name": name,
                "labels": dict(labels),
                "count": hist.count,
                "sum_ms": round(hist.total, 2),
                "avg_ms": round(hist.total / hist.count, 2) if hist.count else 0.0,
                "max_ms": round(hist.maximum, 2),
            }
            for (name, labels), hist in sorted(_histograms.items())
        ]
    return {
        "uptime_seconds": round(time.time() - _started_at, 1),
        "counters": counters,
        "histograms": histograms,
    }


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{str(v).replace(chr(34), "")}"' for k, v in labels)
    return "{" + inner + "}"


def prometheus_text() -> str:
    """Prometheus exposition format, so no vendor client library is required."""
    lines: list[str] = []
    with _LOCK:
        for (name, labels), value in sorted(_counters.items()):
            lines.append(f"{name}{_format_labels(labels)} {value}")
        for (name, labels), hist in sorted(_histograms.items()):
            for bound in LATENCY_BUCKETS_MS:
                count = sum(v for b, v in hist.buckets.items() if b <= bound)
                bucket_labels = labels + (("le", str(bound)),)
                lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {count}")
            lines.append(f"{name}_bucket{_format_labels(labels + (('le', '+Inf'),))} {hist.count}")
            lines.append(f"{name}_sum{_format_labels(labels)} {round(hist.total, 2)}")
            lines.append(f"{name}_count{_format_labels(labels)} {hist.count}")
    lines.append(f"process_uptime_seconds {round(time.time() - _started_at, 1)}")
    return "\n".join(lines) + "\n"
