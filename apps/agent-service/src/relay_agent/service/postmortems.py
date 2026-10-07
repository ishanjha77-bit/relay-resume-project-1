"""Postmortems: every incident a person resolves (with a postmortem requested) gets one."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from relay_agent.config import Settings
from relay_agent.events import StreamSink
from relay_agent.graph.postmortem import PostmortemWriter, markdown
from relay_agent.llm.types import LLMProvider
from relay_agent.service.approvals import StreamConsumer
from relay_agent.service.runner import llm_event
from relay_agent.tracing import TracedProvider, tracer

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)


class IncidentResolved(BaseModel):
    """contracts/schemas/incident-resolved.schema.json"""

    event_id: str
    type: Literal["incident.resolved"]
    at: str
    incident: dict[str, Any]


class Postmortems:
    """Writes the postmortem of a resolved incident, once.

    A lease in Redis keeps a redelivered message from writing it twice. The
    writer's model call and the postmortem itself go out on the agent-events
    stream under a run of their own (agent "postmortem"), so they never mix
    with the investigation's trace.
    """

    LEASE_S = 15 * 60
    DONE_TTL_S = 7 * 24 * 3600

    def __init__(self, settings: Settings, redis: Redis, provider: LLMProvider):
        self._settings = settings
        self._redis = redis
        self._writer = PostmortemWriter(TracedProvider(provider), settings.postmortem)

    async def __call__(self, message: IncidentResolved) -> None:
        incident = message.incident
        key = f"relay:agent:postmortem:{incident['id']}"
        if not await self._redis.set(key, "running", nx=True, ex=self.LEASE_S):
            log.info("postmortem of %s already handled", incident.get("key"))
            return
        run_id = f"pm-{uuid.uuid4().hex[:12]}"
        sink = StreamSink(
            self._redis,
            self._settings.streams.agent_events,
            run_id=run_id,
            incident_id=incident["id"],
            agent="postmortem",
            max_len=self._settings.streams.agent_events_max_len,
        )
        try:
            with tracer.start_as_current_span("postmortem", attributes={"relay.incident.id": incident["id"]}):
                postmortem, response = await self._writer.write(incident)
        except (RedisConnectionError, RedisTimeoutError):
            raise
        except Exception:
            await self._redis.delete(key)  # let a later delivery try again
            raise
        await sink.emit("llm.completed", **llm_event(response))
        if postmortem is None:
            log.warning("the model wrote no usable postmortem for %s", incident.get("key"))
            await self._redis.delete(key)
            return
        await sink.emit(
            "postmortem.written",
            model=response.model,
            postmortem=postmortem.model_dump(mode="json"),
            markdown=markdown(postmortem, incident, response.model),
        )
        await self._redis.set(key, f"done:{run_id}", ex=self.DONE_TTL_S)
        log.info("postmortem of %s written: %s", incident.get("key"), postmortem.title)


class PostmortemConsumer(StreamConsumer[IncidentResolved]):
    """`relay.resolved`: the incidents to write postmortems for."""

    def __init__(self, redis: Redis, settings: Settings, provider: LLMProvider, consumer: str, **kwargs: Any):
        super().__init__(
            redis,
            settings.streams.resolved,
            settings.streams.consumer_group,
            IncidentResolved,
            Postmortems(settings, redis, provider),
            consumer,
            label="incident.resolved",
            **kwargs,
        )
