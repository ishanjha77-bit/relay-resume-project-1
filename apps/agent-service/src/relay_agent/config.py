"""Agent-service settings, read from RELAY_* environment variables and `.env`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServerConfig(BaseModel):
    name: str = Field(description="Tool namespace prefix: tools appear to the model as <name>__<tool>.")
    url: str
    token: SecretStr


Provider = Literal["gemini", "anthropic"]


class ModelRoute(BaseModel):
    """Which model serves an agent role, and how hard it thinks. The provider
    follows from the model name unless set: gemini-* and gemma-* are Google, claude-* Anthropic."""

    model: str
    provider: Provider | None = None
    # Tried in order when `model` can't answer: busy, or out of its daily quota (Gemini).
    fallbacks: list[str] = []
    effort: str | None = "medium"
    max_tokens: int = 16_000

    @model_validator(mode="after")
    def _infer_provider(self) -> ModelRoute:
        if self.provider is None:
            if self.model.startswith(("gemini-", "gemma-")):
                self.provider = "gemini"
            elif self.model.startswith("claude-"):
                self.provider = "anthropic"
            else:
                raise ValueError(f"can't tell which provider serves {self.model!r}: set provider too")
        return self


class Budget(BaseModel):
    """Hard limits per investigation, so a confused agent can't loop or overspend."""

    tool_calls: int = 15
    cost_usd: float = 2.00


class Streams(BaseModel):
    """The Redis Streams shared with platform-api (see contracts/README.md)."""

    incidents: str = "relay.incidents"
    agent_events: str = "relay.agent-events"
    dead_letter: str = "relay.incidents.dead"
    # Human decisions on what the fixer asked to do (contracts/schemas/approval-decision.schema.json).
    approvals: str = "relay.approvals"
    # Incidents a person resolved and wants a postmortem of (contracts/schemas/incident-resolved.schema.json).
    resolved: str = "relay.resolved"
    consumer_group: str = "agent-service"
    # Approximate cap on the agent-events stream; platform-api persists every event.
    agent_events_max_len: int = 100_000


class ServiceSettings(BaseModel):
    """`relay-agent serve`: how many investigations run at once, and for how long."""

    max_concurrent_runs: int = 2
    run_timeout_s: int = 600
    # Crashed deliveries of one incident before it is dead-lettered.
    max_deliveries: int = 3
    # On shutdown, how long running investigations may finish before they are
    # cancelled (and later retried from the stream).
    shutdown_grace_s: float = 25.0
    # Bearer token for POST /runs and GET /runs/{id}/recording; both are off without one.
    api_token: SecretStr | None = None
    # Keep a replayable recording of every run here (see relay_agent.recording).
    record_dir: str | None = None
    keep_recordings: int = 200
    log_level: str = "INFO"


class FixerSettings(BaseModel):
    """The fixer proposes undoing the change behind a bad deploy or config incident,
    then waits for a human; see relay_agent.graph.fixer."""

    enabled: bool = True
    # How long a run waits for its approval; after that the proposal is dropped.
    checkpoint_ttl_minutes: int = 7 * 24 * 60


def _default_mcp_servers() -> list[McpServerConfig]:
    # Local development: MCP servers started with `make mcp-local`.
    return [
        McpServerConfig(name="logs", url="http://localhost:8101/mcp", token=SecretStr("dev-logs-token")),
        McpServerConfig(
            name="metrics", url="http://localhost:8102/mcp", token=SecretStr("dev-metrics-token")
        ),
        McpServerConfig(name="k8s", url="http://localhost:8103/mcp", token=SecretStr("dev-k8s-token")),
        McpServerConfig(
            name="runbooks", url="http://localhost:8104/mcp", token=SecretStr("dev-runbooks-token")
        ),
        # The deploy repo lives on the cluster's Gitea; unreachable locally, so skipped there.
        McpServerConfig(name="github", url="http://localhost:8105/mcp", token=SecretStr("dev-github-token")),
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RELAY_",
        env_nested_delimiter="__",
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    # Free key: https://aistudio.google.com/apikey. Claude needs ANTHROPIC_API_KEY instead.
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    mcp_servers: list[McpServerConfig] = Field(default_factory=_default_mcp_servers)
    # Gemini's free tier by default (docs/adr/0010): each model has its own daily
    # quota, so more models mean more free investigations. claude-opus-5-5 works too.
    investigator: ModelRoute = ModelRoute(
        model="gemini-3.6-flash",
        fallbacks=[
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-3-flash-preview",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ],
        effort="medium",
    )
    # Triage: one small-model call per incident, before the investigation (graph/triage.py).
    # Not Gemma: on a live triage it fell into a repetition loop (docs/adr/0013).
    triage: ModelRoute = ModelRoute(
        model="gemini-3.5-flash-lite",
        fallbacks=["gemini-3.1-flash-lite", "gemini-3-flash-preview"],
        effort="low",
        max_tokens=2_000,
    )
    triage_enabled: bool = True
    # The reviewer: one call per investigation, checking claims against evidence (graph/reviewer.py).
    # Flash models only: reviewing on Flash-Lite, a weaker model than the investigator's, was
    # noise (docs/adr/0013). With none left, there is no review and the verdict stands.
    reviewer: ModelRoute = ModelRoute(
        model="gemini-3.6-flash",
        fallbacks=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3-flash-preview"],
        effort="low",
        max_tokens=4_000,
    )
    reviewer_enabled: bool = True
    # The postmortem writer: one call per resolved incident. Lite models first: the
    # stronger ones are worth more to investigations.
    postmortem: ModelRoute = ModelRoute(
        model="gemini-3.5-flash-lite",
        fallbacks=["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.6-flash"],
        effort="low",
        max_tokens=6_000,
    )
    postmortems_enabled: bool = True
    budget: Budget = Budget()
    alertmanager_url: str = "http://localhost:9093"
    sandbox_namespace: str = "sandbox"
    # Relay's Redis, published on localhost by the kind cluster.
    redis_url: str = "redis://localhost:16379/0"
    streams: Streams = Streams()
    service: ServiceSettings = ServiceSettings()
    fixer: FixerSettings = FixerSettings()
