import anyio
import pytest
from pydantic import SecretStr
from relay_agent.config import McpServerConfig
from relay_agent.tools.mcp_toolbox import McpToolbox


def test_no_reachable_server_is_an_error_naming_them() -> None:
    servers = [
        McpServerConfig(name="logs", url="http://127.0.0.1:1/mcp", token=SecretStr("t")),
        McpServerConfig(name="metrics", url="http://127.0.0.1:2/mcp", token=SecretStr("t")),
    ]

    async def connect() -> None:
        async with McpToolbox(servers):
            pass

    with pytest.raises(RuntimeError, match="no MCP server reachable: logs, metrics"):
        anyio.run(connect)
