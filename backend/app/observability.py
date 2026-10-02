from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque


@dataclass
class HttpTrace:
    ts: datetime
    path: str
    method: str
    status_code: int
    latency_ms: int


class PerfTracker:
    def __init__(self, max_items: int = 5000) -> None:
        self._http_traces: Deque[HttpTrace] = deque(maxlen=max_items)

    def record_http(self, path: str, method: str, status_code: int, latency_ms: int) -> None:
        self._http_traces.append(
            HttpTrace(
                ts=datetime.now(timezone.utc),
                path=path,
                method=method,
                status_code=status_code,
                latency_ms=max(0, int(latency_ms)),
            )
        )

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        one_min = now - timedelta(minutes=1)
        five_min = now - timedelta(minutes=5)

        last_1m = [t for t in self._http_traces if t.ts >= one_min]
        last_5m = [t for t in self._http_traces if t.ts >= five_min]

        latencies = sorted(t.latency_ms for t in last_5m)
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        error_count = sum(1 for t in last_5m if t.status_code >= 500)
        total_5m = len(last_5m)
        error_rate = (error_count / total_5m) if total_5m > 0 else 0.0

        return {
            "requests_1m": len(last_1m),
            "requests_5m": total_5m,
            "error_count_5m": error_count,
            "error_rate_5m": round(error_rate, 4),
            "latency_p50_ms_5m": p50,
            "latency_p95_ms_5m": p95,
            "slow_requests_5m": sum(1 for t in last_5m if t.latency_ms >= 1000),
        }


def percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    pct = max(0, min(100, pct))
    idx = int(round((pct / 100) * (len(values) - 1)))
    return values[idx]


perf_tracker = PerfTracker()
