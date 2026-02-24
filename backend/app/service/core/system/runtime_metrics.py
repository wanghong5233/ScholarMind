"""In-memory runtime metrics for admin ops dashboard."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque


class RuntimeMetrics:
    """Collect lightweight runtime metrics since service startup."""

    def __init__(self, *, window_seconds: int = 60) -> None:
        self._window_seconds = max(10, int(window_seconds))
        self._lock = Lock()
        self._requests_total = 0
        self._requests_4xx = 0
        self._requests_5xx = 0
        self._latency_total_ms = 0.0
        self._recent_request_ts: Deque[float] = deque()
        self._recent_error_ts: Deque[float] = deque()

    def _trim(self, now_ts: float) -> None:
        threshold = now_ts - self._window_seconds
        while self._recent_request_ts and self._recent_request_ts[0] < threshold:
            self._recent_request_ts.popleft()
        while self._recent_error_ts and self._recent_error_ts[0] < threshold:
            self._recent_error_ts.popleft()

    def record_request(self, *, status_code: int, latency_ms: float) -> None:
        now_ts = time.time()
        with self._lock:
            self._requests_total += 1
            if 400 <= status_code < 500:
                self._requests_4xx += 1
            elif status_code >= 500:
                self._requests_5xx += 1
            self._latency_total_ms += max(0.0, float(latency_ms))
            self._recent_request_ts.append(now_ts)
            if status_code >= 500:
                self._recent_error_ts.append(now_ts)
            self._trim(now_ts)

    def snapshot(self, *, uptime_secs: int) -> dict:
        now_ts = time.time()
        with self._lock:
            self._trim(now_ts)
            requests_1m = len(self._recent_request_ts)
            errors_1m = len(self._recent_error_ts)
            avg_latency_ms = (
                self._latency_total_ms / self._requests_total
                if self._requests_total > 0
                else 0.0
            )
            qps_avg = (
                self._requests_total / max(int(uptime_secs), 1)
                if uptime_secs > 0
                else 0.0
            )
            qps_1m = requests_1m / float(self._window_seconds)
            error_rate_5xx = (
                self._requests_5xx / self._requests_total
                if self._requests_total > 0
                else 0.0
            )
            error_rate_1m = errors_1m / requests_1m if requests_1m > 0 else 0.0
            return {
                "requests_total": self._requests_total,
                "requests_4xx_total": self._requests_4xx,
                "requests_5xx_total": self._requests_5xx,
                "avg_latency_ms": round(avg_latency_ms, 3),
                "qps_avg": round(qps_avg, 4),
                "qps_1m": round(qps_1m, 4),
                "error_rate_5xx": round(error_rate_5xx, 6),
                "error_rate_1m": round(error_rate_1m, 6),
                "window_seconds": self._window_seconds,
            }


runtime_metrics = RuntimeMetrics()

