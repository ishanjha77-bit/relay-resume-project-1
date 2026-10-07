"""Turn raw log lines into compact, redacted records.

Our services log JSON (logstash layout in Java, the same field names in Go);
Postgres and JVM bootstrap lines are plain text. Either way the model gets the
same small shape, with secrets masked and stack traces cut to the frames that
locate the failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from relay_mcp_kit import clip, redact

from logs_loki.loki import LogEntry, iso

# Fields every line has, or that we surface explicitly; everything else is an attribute.
_KNOWN_FIELDS = {
    "@timestamp",
    "@version",
    "time",
    "message",
    "msg",
    "level",
    "level_value",
    "logger_name",
    "thread_name",
    "service",
    "trace_id",
    "span_id",
    "trace_flags",
    "stack_trace",
}
_FRAMES_PER_EXCEPTION = 4


@dataclass
class Record:
    ts: datetime
    service: str
    pod: str
    version: str | None
    level: str
    message: str
    logger: str | None = None
    trace_id: str | None = None
    stack: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ts": iso_ms(self.ts),
            "service": self.service,
            "level": self.level,
            "message": self.message,
        }
        for key in ("logger", "trace_id", "stack"):
            if value := getattr(self, key):
                out[key] = value
        if self.attrs:
            out["attrs"] = self.attrs
        out["pod"] = self.pod
        if self.version:
            out["version"] = self.version
        return out


def iso_ms(ts: datetime) -> str:
    return iso(ts)[:-1] + f".{ts.microsecond // 1000:03d}Z"


def parse(entry: LogEntry, message_limit: int = 400) -> Record:
    labels = entry.labels
    service = labels.get("service_name") or labels.get("k8s_container_name") or "unknown"
    record = Record(
        ts=entry.ts,
        service=service,
        pod=labels.get("k8s_pod_name", ""),
        version=labels.get("service_version"),
        level=(labels.get("detected_level") or "unknown").upper(),
        message="",
    )
    data = _json_object(entry.line)
    if data is None:
        record.message = clip(redact(entry.line.strip()), message_limit)
        return record

    record.message = clip(redact(str(data.get("message") or data.get("msg") or "")), message_limit)
    if level := data.get("level"):
        record.level = str(level).upper()
    if logger := data.get("logger_name"):
        record.logger = clip(str(logger), 100)
    if trace_id := data.get("trace_id"):
        record.trace_id = str(trace_id)
    if stack := data.get("stack_trace"):
        record.stack = trim_stack(redact(str(stack)))
    record.attrs = {
        key: clip(redact(str(value)), 300)
        for key, value in data.items()
        if key not in _KNOWN_FIELDS and value not in (None, "")
    }
    return record


def trim_stack(stack: str) -> str:
    """Keep every exception header and the top frames of each.

    Java chains put the real cause in the last ``Caused by:`` section, so frames
    are capped per section rather than overall.
    """
    kept: list[str] = []
    section_frames = 0
    dropped = 0
    for raw in stack.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("..."):
            continue
        if line.startswith("at "):
            section_frames += 1
            if section_frames > _FRAMES_PER_EXCEPTION:
                dropped += 1
                continue
        else:
            section_frames = 0
        kept.append(line)
    if dropped:
        kept.append(f"... {dropped} more frames")
    return clip("\n".join(kept), 1500)


def _json_object(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
