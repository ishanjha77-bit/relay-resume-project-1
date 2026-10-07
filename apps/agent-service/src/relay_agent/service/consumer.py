"""The incident consumer: every incident platform-api opens gets one investigation."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from relay_agent.config import ServiceSettings, Streams
from relay_agent.graph.investigator import InvestigationState
from relay_agent.graph.schemas import Incident
from relay_agent.service.metrics import MESSAGES, RUNS_IN_FLIGHT

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)

Run = Callable[[Incident, str], Awaitable[InvestigationState]]


class IncidentOpened(BaseModel):
    """contracts/schemas/incident-opened.schema.json"""

    event_id: str
    type: Literal["incident.opened"]
    at: str
    incident: Incident


@dataclass
class ActiveRun:
    run_id: str
    incident_id: str
    incident_number: int | None
    title: str
    source: Literal["stream", "api"]
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class Busy(Exception):
    """Every run slot is taken."""


class AlreadyRunning(Exception):
    """The incident is being investigated right now."""


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


class IncidentConsumer:
    """Reads `relay.incidents` as a member of the `agent-service` consumer group.

    Delivery is at-least-once, so each incident is guarded by a key in Redis:

    - Before a run, the consumer takes a lease on the incident (SET NX with a
      TTL). A second copy of the message, read while the first is still
      running, finds the lease and is acknowledged as a duplicate. So is a copy
      redelivered after the run finished, which finds the lease marked done.
    - A message is acknowledged once its investigation has finished, whatever
      the verdict. A failed investigation is still finished: it reported
      `run.failed`, and rerunning it automatically would only double the spend.
    - A run that couldn't publish its events because Redis failed stays pending,
      as does the message of a worker that crashed. The reclaimer runs those
      again once they've been idle longer than any run can take. After
      `max_deliveries` attempts a message is dead-lettered.
    - A malformed message can never succeed, so it is dead-lettered at once.
    """

    DONE_TTL_S = 7 * 24 * 3600
    RECLAIM_EVERY_S = 30.0
    BLOCK_MS = 5_000

    def __init__(
        self,
        redis: Redis,
        streams: Streams,
        service: ServiceSettings,
        run: Run,
        consumer: str,
        *,
        block_ms: int = BLOCK_MS,
        reclaim_idle_ms: int | None = None,
    ):
        self._redis = redis
        self._stream = streams.incidents
        self._group = streams.consumer_group
        self._dead_letter_stream = streams.dead_letter
        self._max_runs = service.max_concurrent_runs
        self._max_deliveries = service.max_deliveries
        self._run = run
        self._block_ms = block_ms
        self.consumer = consumer
        # The lease outlives any run (the runner enforces run_timeout_s), and the
        # reclaimer waits for the lease to lapse before taking over a message.
        self._lease_s = service.run_timeout_s + 30
        self.reclaim_idle_ms = (
            reclaim_idle_ms if reclaim_idle_ms is not None else (service.run_timeout_s + 60) * 1000
        )
        self.active: dict[str, ActiveRun] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._in_flight: set[str] = set()  # message ids being processed here
        self._last_reclaim = 0.0

    @property
    def free_slots(self) -> int:
        return self._max_runs - len(self._tasks)

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        """Consume until cancelled, riding out Redis outages."""
        backoff = 1.0
        started = False
        while True:
            try:
                if not started:
                    await self.ensure_group()
                    # Whatever a previous life of this consumer read but never finished.
                    await self.read(pending=True)
                    started = True
                    log.info("consuming %s as %s/%s", self._stream, self._group, self.consumer)
                if self.free_slots <= 0:
                    await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
                    continue
                if not await self.read():
                    # BLOCK already waited; this only keeps a server that ignores it from spinning.
                    await asyncio.sleep(0.05)
                if time.monotonic() - self._last_reclaim >= self.RECLAIM_EVERY_S:
                    await self.reclaim()
                backoff = 1.0
            except (RedisConnectionError, RedisTimeoutError) as e:
                log.warning("Redis unavailable (%s); retrying in %.0f s", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except ResponseError as e:
                if "NOGROUP" not in str(e):
                    raise
                # Redis came back without its data (a wiped volume, or no persistence).
                log.warning("consumer group %s is gone; recreating it", self._group)
                started = False

    async def ensure_group(self) -> None:
        try:
            # MKSTREAM: platform-api may not have published anything yet.
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def read(self, *, pending: bool = False) -> int:
        """Start runs for new messages, up to the free run slots; or, with
        `pending`, for messages this consumer was given earlier and never acknowledged."""
        if self.free_slots <= 0:
            return 0
        response = await self._redis.xreadgroup(
            self._group,
            self.consumer,
            {self._stream: "0" if pending else ">"},
            count=self.free_slots,
            block=None if pending else self._block_ms,
        )
        started = 0
        for _, messages in response or []:
            for message_id, fields in messages:
                if message_id in self._in_flight:
                    continue
                self._track(self._process(message_id, fields), message_id)
                started += 1
        return started

    async def reclaim(self) -> int:
        """Take over messages a crashed or failed run left pending."""
        self._last_reclaim = time.monotonic()
        started = 0
        pending = await self._redis.xpending_range(self._stream, self._group, min="-", max="+", count=100)
        for entry in pending:
            message_id = entry["message_id"]
            if message_id in self._in_flight or entry["time_since_delivered"] < self.reclaim_idle_ms:
                continue
            if entry["times_delivered"] >= self._max_deliveries:
                await self._dead_letter_pending(message_id, entry["times_delivered"])
                continue
            if self.free_slots <= 0:
                break
            claimed = await self._redis.xclaim(
                self._stream,
                self._group,
                self.consumer,
                min_idle_time=self.reclaim_idle_ms,
                message_ids=[message_id],
            )
            for claimed_id, fields in claimed:
                log.warning(
                    "reclaimed %s from %s after %d deliveries",
                    claimed_id,
                    entry["consumer"],
                    entry["times_delivered"],
                )
                self._track(self._process(claimed_id, fields), claimed_id)
                started += 1
        return started

    async def drain(self, timeout_s: float) -> None:
        """Let running investigations finish; cancel what is left after `timeout_s`.
        A cancelled run's message stays pending and is reclaimed later."""
        if not self._tasks:
            return
        _, unfinished = await asyncio.wait(set(self._tasks), timeout=timeout_s)
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*set(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------- one message
    async def _process(self, message_id: str, fields: dict[str, Any] | None) -> None:
        raw = (fields or {}).get("event")
        if raw is None:
            if fields is None:  # trimmed from the stream before we got to it
                await self._ack(message_id)
                return
            await self._dead_letter(message_id, fields, "no `event` field")
            return
        try:
            incident = IncidentOpened.model_validate_json(raw).incident
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'message'}: {err['msg']}" for err in e.errors()[:5]
            )
            await self._dead_letter(message_id, fields or {}, f"malformed incident.opened: {problems}")
            return

        run_id = new_run_id()
        key = self._key(incident.id)
        if not await self._redis.set(key, f"running:{run_id}", nx=True, ex=self._lease_s):
            holder = await self._redis.get(key)
            log.info(
                "incident %s already handled (%s); acknowledging duplicate %s",
                incident.id,
                holder,
                message_id,
                extra={"incident_id": incident.id},
            )
            MESSAGES.labels("duplicate").inc()
            await self._ack(message_id)
            return
        try:
            await self._investigate(incident, run_id, "stream")
        except (RedisConnectionError, RedisTimeoutError) as e:
            MESSAGES.labels("retry").inc()
            log.warning(
                "run %s could not report (%s); message %s stays pending for a retry",
                run_id,
                e,
                message_id,
                extra={"run_id": run_id, "incident_id": incident.id},
            )
            return
        await self._ack(message_id)
        MESSAGES.labels("investigated").inc()

    async def _investigate(self, incident: Incident, run_id: str, source: Literal["stream", "api"]) -> None:
        self.active[run_id] = ActiveRun(run_id, incident.id, incident.number, incident.title, source)
        RUNS_IN_FLIGHT.inc()
        try:
            await self._run(incident, run_id)
        finally:
            RUNS_IN_FLIGHT.dec()
            del self.active[run_id]
        await self._redis.set(self._key(incident.id), f"done:{run_id}", ex=self.DONE_TTL_S)

    # ---------------------------------------------------------------- API runs
    async def start(self, incident: Incident) -> str:
        """Investigate on request (POST /runs) rather than from the stream; this
        also reruns an incident that was already investigated."""
        if self.free_slots <= 0:
            raise Busy
        run_id = new_run_id()
        key = self._key(incident.id)
        if not await self._redis.set(key, f"running:{run_id}", nx=True, ex=self._lease_s):
            holder = await self._redis.get(key) or ""
            if holder.startswith("running:"):
                raise AlreadyRunning(holder.removeprefix("running:"))
            await self._redis.set(key, f"running:{run_id}", ex=self._lease_s)
        self._track(self._investigate(incident, run_id, "api"), None)
        return run_id

    # ----------------------------------------------------------------- helpers
    def _track(self, work: Coroutine[Any, Any, None], message_id: str | None) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        if message_id:
            self._in_flight.add(message_id)

        def finished(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            if message_id:
                self._in_flight.discard(message_id)
            if not t.cancelled() and t.exception():
                log.error("run task failed", exc_info=t.exception())

        task.add_done_callback(finished)

    @staticmethod
    def _key(incident_id: str) -> str:
        return f"relay:agent:incident:{incident_id}"

    async def _ack(self, message_id: str) -> None:
        await self._redis.xack(self._stream, self._group, message_id)

    async def _dead_letter(self, message_id: str, fields: dict[str, Any], reason: str) -> None:
        log.error("dead-lettering %s: %s", message_id, reason)
        await self._redis.xadd(
            self._dead_letter_stream,
            {**fields, "reason": reason, "message_id": message_id, "stream": self._stream},
        )
        await self._ack(message_id)
        MESSAGES.labels("dead_lettered").inc()

    async def _dead_letter_pending(self, message_id: str, deliveries: int) -> None:
        entries = await self._redis.xrange(self._stream, min=message_id, max=message_id)
        if not entries:  # trimmed: nothing left to keep
            await self._ack(message_id)
            return
        await self._dead_letter(message_id, entries[0][1], f"gave up after {deliveries} deliveries")
