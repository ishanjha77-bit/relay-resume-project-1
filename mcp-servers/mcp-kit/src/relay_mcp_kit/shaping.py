"""Keep tool output small, safe and useful before it reaches a model.

Every byte a tool returns is paid for as input tokens on every later turn of
the investigation, and anything it returns may contain secrets or text written
by an attacker. These helpers run on every MCP server response.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization headers and bearer tokens
    (re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    # key=value / key: value secrets
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
            r"(\"?\s*[:=]\s*\"?)[^\s\"',;&]{3,}"
        ),
        r"\1\2[REDACTED]",
    ),
    # Credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@"), r"\1\2:[REDACTED]@"),
    # JWTs
    (re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"), "[REDACTED_JWT]"),
    # Well-known key formats
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(sk|rk|pk)-(ant|live|test|proj)-[A-Za-z0-9_-]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
]


def redact(text: str) -> str:
    """Mask credentials that services sometimes log by accident."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def clip(text: str, limit: int) -> str:
    """Truncate to ``limit`` characters, saying how much was dropped."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[+{len(text) - limit} chars]"


_VOLATILE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b"), "<time>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b(?:pay|ord|req|txn)_[0-9a-zA-Z]+\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\bSKU-\d+\b"), "<sku>"),
    (re.compile(r"\bc-\d+\b"), "<customer>"),
    (re.compile(r"@[0-9a-f]{6,}\b", re.I), "@<obj>"),
    (re.compile(r"\b\d+(\.\d+)?\s?(ms|s|µs|us|ns|m)\b"), "<duration>"),
    (re.compile(r"\b\d+\b"), "<n>"),
]


def normalize_message(message: str) -> str:
    """Reduce a log message to its template so similar lines group together.

    ``"Connection is not available, request timed out after 2000ms (total=10)"``
    becomes ``"Connection is not available, request timed out after <duration> (total=<n>)"``.
    """
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    for pattern, placeholder in _VOLATILE:
        first_line = pattern.sub(placeholder, first_line)
    return clip(first_line, 300)


def to_json(value: Any) -> str:
    """Compact, deterministic JSON for tool results."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False, default=str)
