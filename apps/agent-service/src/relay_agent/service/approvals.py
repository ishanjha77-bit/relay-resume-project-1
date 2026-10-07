"""Small streams the agent service consumes one message at a time: approval decisions
(which resume paused runs) and resolved incidents (which get a postmortem)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from relay_agent.config import Streams

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)


class ApprovalDecision(BaseModel):
    """contracts/schemas/approval-decision.schema.json"""

    approval_id: str
    incident_id: str
    run_id: str
    decision: Literal["approved", "rejected"]
    decided_by: str
    reason: str | None = None
    decided_at: str
    action: str
    token: str | None = None


class StreamConsumer[M: BaseModel]:
    """Reads one stream as a member of the `agent-service` group, one message at a time.

    For streams whose messages are rare and quick to handle. A message is
    acknowledged once handled, whatever the outcome: handlers report their own
    failures (on the agent-events stream) and are idempotent. A message that never
    parses is dead-lettered to `<stream>.dead`. Redis outages are ridden out, and
    what this consumer read but never acknowledged is re-read when it reconnects.
    """

    BLOCK_MS = 5_000

    def __init__(
        self,
        redis: Redis,
        stream: str,
        group: str,
        model: type[M],
        handle: Callable[[M], Awaitable[Any]],
        consumer: str,
        *,
        label: str,
        block_ms: int = BLOCK_MS,
    ):
        self._redis = redis
        self._stream = stream
        self._group = group
        self._dead_letter_stream = f"{stream}.dead"
        self._model = model
        self._handle = handle
        self._label = label
        self._block_ms = block_ms
        self.consumer = consumer

    async def run_forever(self) -> None:
        backoff = 1.0
        pending = True  # first, whatever an earlier life of this consumer left unacknowledged
        while True:
            try:
                if pending:
                    await self.ensure_group()
                processed = await self.read(pending=pending)
                if pending:
                    log.info("consuming %s as %s/%s", self._stream, self._group, self.consumer)
                pending = False
                if not processed:
                    await asyncio.sleep(0.05)  # BLOCK waited already; this guards against servers that don't
                backoff = 1.0
            except (RedisConnectionError, RedisTimeoutError) as e:
                log.warning("Redis unavailable (%s); retrying in %.0f s", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                pending = True
            except ResponseError as e:
                if "NOGROUP" not in str(e):
                    raise
                pending = True

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def read(self, *, pending: bool = False) -> int:
        response = await self._redis.xreadgroup(
            self._group,
            self.consumer,
            {self._stream: "0" if pending else ">"},
            count=10,
            block=None if pending else self._block_ms,
        )
        processed = 0
        for _, messages in response or []:
            for message_id, fields in messages:
                await self._process(message_id, fields)
                processed += 1
        return processed

    async def _process(self, message_id: str, fields: dict[str, Any] | None) -> None:
        raw = (fields or {}).get("event")
        if raw is None:
            if fields is None:  # trimmed before we got to it
                await self._ack(message_id)
                return
            await self._dead_letter(message_id, fields, "no `event` field")
            return
        try:
            message = self._model.model_validate_json(raw)
        except ValidationError as e:
            await self._dead_letter(
                message_id, fields or {}, f"malformed {self._label}: {e.error_count()} problems"
            )
            return
        try:
            await self._handle(message)
        except (RedisConnectionError, RedisTimeoutError):
            raise  # stays pending; re-read after reconnecting
        except Exception:
            log.exception("handling a %s failed", self._label)
        await self._ack(message_id)

    async def _ack(self, message_id: str) -> None:
        await self._redis.xack(self._stream, self._group, message_id)

    async def _dead_letter(self, message_id: str, fields: dict[str, Any], reason: str) -> None:
        log.error("dead-lettering %s: %s", message_id, reason)
        await self._redis.xadd(
            self._dead_letter_stream, {**fields, "reason": reason, "message_id": message_id}
        )
        await self._ack(message_id)


class ApprovalConsumer(StreamConsumer[ApprovalDecision]):
    """`relay.approvals`: a human's decision resumes the run that asked for it."""

    def __init__(
        self,
        redis: Redis,
        streams: Streams,
        resume: Callable[[ApprovalDecision], Awaitable[Any]],
        consumer: str,
        *,
        block_ms: int = StreamConsumer.BLOCK_MS,
    ):
        super().__init__(
            redis,
            streams.approvals,
            streams.consumer_group,
            ApprovalDecision,
            resume,
            consumer,
            label="approval decision",
            block_ms=block_ms,
        )
