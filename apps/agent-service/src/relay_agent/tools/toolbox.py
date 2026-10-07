"""Tool specs, outcomes and the per-role tool policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SEPARATOR = "__"  # tool names reach the model as <server>__<tool>, e.g. logs__search_logs


@dataclass(frozen=True)
class ToolSpec:
    name: str
    server: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool

    def to_anthropic(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class Toolbox(Protocol):
    specs: list[ToolSpec]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


# Which kinds of tool each agent role may see. Write tools are never handed to
# an investigating agent; they run only after a human approves the exact action.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "investigator": frozenset({"read"}),
    "triage": frozenset({"read"}),
    "reviewer": frozenset(),
}


def allowed_for(role: str, specs: list[ToolSpec]) -> list[ToolSpec]:
    scopes = ROLE_SCOPES.get(role, frozenset())
    return [s for s in specs if ("read" if s.read_only else "write") in scopes]
