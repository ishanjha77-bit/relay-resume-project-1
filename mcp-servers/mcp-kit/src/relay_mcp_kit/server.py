"""Build and run an MCP server the way every Relay MCP server runs.

Streamable HTTP in stateless JSON mode (no session affinity, so servers scale
horizontally), bearer auth via ``StaticTokenVerifier``, DNS-rebinding
protection with an explicit host allowlist, a ``/healthz`` route for probes,
and JSON logs on stdout like the rest of the platform.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from relay_mcp_kit.auth import StaticTokenVerifier


@dataclass(frozen=True)
class KitSettings:
    """Settings every MCP server reads from its environment."""

    token: str
    port: int = 8000
    public_url: str = "http://localhost:8000/mcp"
    issuer_url: str = "http://platform-api.relay.svc.cluster.local:8080"
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost:*", "127.0.0.1:*"])
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> KitSettings:
        port = int(os.environ.get("PORT", "8000"))
        hosts = os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*")
        return cls(
            token=os.environ.get("MCP_TOKEN", ""),
            port=port,
            public_url=os.environ.get("MCP_PUBLIC_URL", f"http://localhost:{port}/mcp"),
            issuer_url=os.environ.get("MCP_ISSUER_URL", cls.issuer_url),
            allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )


def create_server(
    name: str,
    *,
    instructions: str,
    scopes: Sequence[str],
    version: str = "0.1.0",
    settings: KitSettings | None = None,
    lifespan: Callable[[MCPServer], AbstractAsyncContextManager[Any]] | None = None,
) -> MCPServer:
    """An MCP server with Relay's auth and probes. ``lifespan`` runs once per
    process (the HTTP session manager enters it at startup), so it can own
    connection pools and background work."""
    settings = settings or KitSettings.from_env()
    configure_logging(name, settings.log_level)
    mcp = MCPServer(
        name,
        instructions=instructions,
        version=version,
        token_verifier=StaticTokenVerifier(settings.token, scopes, resource=settings.public_url),
        auth=AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.public_url,
            required_scopes=list(scopes),
            validate_token_resource=True,
        ),
        lifespan=lifespan,
    )

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


def serve(mcp: MCPServer, settings: KitSettings | None = None) -> None:
    settings = settings or KitSettings.from_env()
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[],
        ),
    )
    # Windows only (local dev): psycopg's async driver needs a selector loop,
    # and uvicorn would pick the Proactor loop there.
    loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_config=None, access_log=False, loop=loop)


class _JsonFormatter(logging.Formatter):
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
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Request-level chatter from the HTTP stack is noise in Loki.
    for noisy in ("httpx2", "httpx", "uvicorn.access", "mcp.server.streamable_http_manager"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
