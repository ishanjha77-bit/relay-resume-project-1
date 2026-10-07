"""Async client for the Prometheus HTTP API."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2


class PrometheusError(RuntimeError):
    """Prometheus rejected or failed a query; the message is safe to show the model."""


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    @classmethod
    def ending(cls, end: str | None, minutes: int) -> TimeWindow:
        minutes = max(1, min(minutes, 24 * 60))
        stop = parse_time(end) if end else datetime.now(UTC)
        return cls(stop - timedelta(minutes=minutes), stop)

    @property
    def minutes(self) -> int:
        return round((self.end - self.start).total_seconds() / 60)

    def describe(self) -> dict[str, str]:
        return {"start": iso(self.start), "end": iso(self.end)}


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"invalid time {value!r}: use ISO-8601, e.g. 2026-10-04T06:10:00Z") from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(ts: datetime | float) -> str:
    if not isinstance(ts, datetime):
        ts = datetime.fromtimestamp(ts, UTC)
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_float(value: str) -> float | None:
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


class PrometheusClient:
    def __init__(self, base_url: str, http: httpx2.AsyncClient | None = None, timeout: float = 15.0):
        self._http = http or httpx2.AsyncClient(base_url=base_url, timeout=timeout)

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        try:
            response = await self._http.get(path, params=params)
        except httpx2.TimeoutException as e:
            raise PrometheusError(
                "Prometheus query timed out; simplify the query or shorten the range"
            ) from e
        except httpx2.RequestError as e:
            raise PrometheusError(f"Prometheus is unreachable: {e.__class__.__name__}") from e
        body = (
            response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        )
        if response.status_code >= 400 or body.get("status") == "error":
            detail = body.get("error") or response.text[:300]
            raise PrometheusError(f"Prometheus returned {response.status_code}: {detail}")
        return body

    async def instant(self, query: str, at: datetime | None = None) -> list[dict]:
        params = {"query": query, "timeout": "10s"}
        if at:
            params["time"] = f"{at.timestamp():.3f}"
        return (await self._get("/api/v1/query", params))["data"]["result"]

    async def range(self, query: str, window: TimeWindow, step_seconds: int) -> list[dict]:
        params = {
            "query": query,
            "start": f"{window.start.timestamp():.3f}",
            "end": f"{window.end.timestamp():.3f}",
            "step": str(step_seconds),
            "timeout": "15s",
        }
        return (await self._get("/api/v1/query_range", params))["data"]["result"]

    async def alerts(self) -> list[dict]:
        return (await self._get("/api/v1/alerts"))["data"]["alerts"]

    async def metric_names(self) -> list[str]:
        return (await self._get("/api/v1/label/__name__/values"))["data"]
