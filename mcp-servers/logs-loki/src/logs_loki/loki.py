"""Async client for the Loki HTTP API and safe LogQL construction.

The model never writes label matchers itself in the curated tools: values are
validated and quoted here, so a service name can't smuggle extra LogQL in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")

# Loki derives `detected_level` for every line (JSON or plain text).
LEVELS = {
    "error": "error|fatal|critical",
    "warn": "warn|warning|error|fatal|critical",
}


class LokiError(RuntimeError):
    """Loki rejected or failed a query; the message is safe to show the model."""


def quote(value: str) -> str:
    """Quote a value as a LogQL double-quoted string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def label_value(value: str, what: str = "service") -> str:
    if not _LABEL_VALUE.match(value):
        raise ValueError(f"invalid {what} {value!r}: use letters, digits, '.', '_' or '-'")
    return value


def stream_selector(namespace: str, service: str | None = None) -> str:
    matchers = [f"k8s_namespace_name={quote(namespace)}"]
    if service:
        matchers.append(f"service_name={quote(label_value(service))}")
    return "{" + ", ".join(matchers) + "}"


def log_query(namespace: str, service: str | None, contains: str | None, level: str | None) -> str:
    query = stream_selector(namespace, service)
    if contains:
        query += f" |= {quote(contains)}"
    if level and level in LEVELS:
        query += f' | detected_level=~"{LEVELS[level]}"'
    return query


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    @classmethod
    def ending(cls, end: str | None, minutes: int) -> TimeWindow:
        minutes = max(1, min(minutes, 360))
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


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ns(ts: datetime) -> str:
    return str(int(ts.timestamp() * 1_000_000_000))


@dataclass(frozen=True)
class LogEntry:
    ts: datetime
    line: str
    labels: dict[str, str]


class LokiClient:
    def __init__(self, base_url: str, http: httpx2.AsyncClient | None = None, timeout: float = 15.0):
        self._http = http or httpx2.AsyncClient(base_url=base_url, timeout=timeout)

    async def _get(self, path: str, params: dict[str, str | int]) -> dict:
        try:
            response = await self._http.get(path, params=params)
        except httpx2.TimeoutException as e:
            raise LokiError("Loki query timed out; narrow the time window or the filter") from e
        except httpx2.RequestError as e:
            raise LokiError(f"Loki is unreachable: {e.__class__.__name__}") from e
        if response.status_code >= 400:
            raise LokiError(f"Loki returned {response.status_code}: {response.text[:300]}")
        return response.json()

    async def logs(self, query: str, window: TimeWindow, limit: int) -> list[LogEntry]:
        """Log lines matching ``query``, newest first."""
        body = await self._get(
            "/loki/api/v1/query_range",
            {
                "query": query,
                "start": _ns(window.start),
                "end": _ns(window.end),
                "limit": limit,
                "direction": "backward",
            },
        )
        entries = [
            LogEntry(datetime.fromtimestamp(int(ts) / 1e9, UTC), line, stream["stream"])
            for stream in body["data"]["result"]
            for ts, line, *_ in stream["values"]
        ]
        entries.sort(key=lambda e: e.ts, reverse=True)
        return entries[:limit]

    async def matrix(self, query: str, window: TimeWindow, step_seconds: int) -> list[dict]:
        """A LogQL metric query over the window: [{"metric": {...}, "values": [[ts, "n"], ...]}]."""
        body = await self._get(
            "/loki/api/v1/query_range",
            {"query": query, "start": _ns(window.start), "end": _ns(window.end), "step": step_seconds},
        )
        return body["data"]["result"]
