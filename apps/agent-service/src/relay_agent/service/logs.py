"""JSON log lines on stdout, like every other Relay service (Loki parses them)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Set through `extra=` (see events.LogSink); added to the line when present.
CONTEXT_KEYS = ("run_id", "incident_id", "incident_number", "event")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "@timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger_name": record.name,
            "service": self.service,
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(service: str = "agent-service", level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx2", "httpx", "uvicorn.access", "mcp.client.streamable_http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
