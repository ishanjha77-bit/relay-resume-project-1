"""Provider-neutral request/response types.

Messages and content blocks use the Anthropic Messages format as the canonical
shape (Claude is the primary provider); other providers translate at their edge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in asdict(self)})

    @property
    def prompt_tokens(self) -> int:
        """Everything the model read: uncached + cache writes + cache reads."""
        return (
            self.input_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
            + self.cache_read_tokens
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class LLMRequest:
    role: str  # agent role, e.g. "investigator"
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    effort: str | None = None
    max_tokens: int = 16_000
    # Models to try, in order, when `model` is unavailable (providers that support it).
    fallbacks: list[str] = field(default_factory=list)
    # No more tools: the model must give its answer now. Providers that can
    # require the answer (rather than just ask for it) do.
    answer_now: bool = False


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]  # content blocks, replayed verbatim in later turns
    stop_reason: str
    model: str  # the model that actually served the request
    usage: Usage
    cost_usd: float
    latency_ms: int
    request_id: str | None = None
    refusal_category: str | None = None

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def progress_notes(self) -> list[str]:
        """Between-tool-call updates (thinking blocks with display="updates")."""
        return [b["thinking"] for b in self.content if b.get("type") == "thinking" and b.get("thinking")]


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
