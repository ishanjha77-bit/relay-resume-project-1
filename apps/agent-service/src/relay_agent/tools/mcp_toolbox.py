"""Connect to the configured MCP servers for the lifetime of one investigation."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from relay_agent.config import McpServerConfig
from relay_agent.tools.toolbox import SEPARATOR, ToolOutcome, ToolSpec

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 45.0


class McpToolbox:
    """Discovers tools from every MCP server and routes calls to the right one.

    Tool order is sorted by name: the tool list is part of the cached (and
    thinking-bound) prompt prefix, so it must be byte-identical on every turn.
    """

    def __init__(self, servers: list[McpServerConfig]):
        self._servers = servers
        self._clients: dict[str, Client] = {}
        self._stack = AsyncExitStack()
        self.specs: list[ToolSpec] = []
        self.unavailable: list[str] = []  # servers that could not be reached; their tools are missing

    async def __aenter__(self) -> McpToolbox:
        specs: list[ToolSpec] = []
        try:
            for server in self._servers:
                try:
                    specs += await self._connect(server)
                except Exception as e:  # one server down must not stop the investigation
                    self.unavailable.append(server.name)
                    log.warning("MCP server %s unavailable: %s: %s", server.name, type(e).__name__, e)
            if not self._clients:
                raise RuntimeError(f"no MCP server reachable: {', '.join(self.unavailable)}")
        except BaseException:
            await self._stack.aclose()
            raise
        self.specs = sorted(specs, key=lambda s: s.name)
        log.info("connected to %d MCP servers, %d tools", len(self._clients), len(self.specs))
        return self

    async def _connect(self, server: McpServerConfig) -> list[ToolSpec]:
        stack = AsyncExitStack()
        try:
            http = create_mcp_http_client(
                headers={"Authorization": f"Bearer {server.token.get_secret_value()}"}
            )
            client = await stack.enter_async_context(
                Client(streamable_http_client(server.url, http_client=http))
            )
            listed = await client.list_tools()
        except BaseException:
            await stack.aclose()
            raise
        self._stack.push_async_callback(stack.aclose)
        self._clients[server.name] = client
        return [
            ToolSpec(
                name=f"{server.name}{SEPARATOR}{tool.name}",
                server=server.name,
                remote_name=tool.name,
                description=(tool.description or "").strip(),
                input_schema=tool.input_schema,
                read_only=bool(tool.annotations and tool.annotations.read_only_hint),
            )
            for tool in listed.tools
        ]

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        spec = next((s for s in self.specs if s.name == name), None)
        if spec is None:
            return ToolOutcome(f"unknown tool {name!r}", is_error=True)
        started = time.monotonic()
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                result = await self._clients[spec.server].call_tool(spec.remote_name, arguments)
        except TimeoutError:
            return ToolOutcome(
                f"{name} timed out after {CALL_TIMEOUT_S:.0f}s",
                is_error=True,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as e:  # transport failures become tool errors the model can react to
            log.warning("tool %s failed: %s", name, e)
            return ToolOutcome(
                f"{name} failed: {e.__class__.__name__}: {e}",
                is_error=True,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        text = "\n".join(
            getattr(block, "text", "") for block in result.content if getattr(block, "type", "") == "text"
        )
        return ToolOutcome(
            text=text, is_error=bool(result.is_error), latency_ms=int((time.monotonic() - started) * 1000)
        )
