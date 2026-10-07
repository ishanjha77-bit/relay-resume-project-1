"""Where runs that wait for a human are kept: LangGraph checkpoints in Relay's Redis."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

log = logging.getLogger(__name__)


class RedisCheckpoints:
    """The fixer's checkpoint store, connected on first use and again after a failure,
    so a Redis that is down when the service starts delays the fixer instead of
    stopping the service. Checkpoints expire after `ttl_minutes`: a proposal
    nobody decides on within that time is dropped."""

    def __init__(self, url: str, ttl_minutes: int):
        self._url = url
        self._ttl = ttl_minutes
        self._lock = asyncio.Lock()
        self._stack = AsyncExitStack()
        self._saver: BaseCheckpointSaver | None = None

    async def __call__(self) -> BaseCheckpointSaver | None:
        if self._saver is not None:
            return self._saver
        async with self._lock:
            if self._saver is not None:
                return self._saver
            stack = AsyncExitStack()
            try:
                saver = await stack.enter_async_context(
                    AsyncRedisSaver(
                        redis_url=self._url,
                        ttl={"default_ttl": self._ttl, "refresh_on_read": True},
                        checkpoint_prefix="relay:fix:checkpoint",
                        checkpoint_write_prefix="relay:fix:write",
                    )
                )
                await saver.asetup()
            except Exception as e:
                await stack.aclose()
                log.warning(
                    "checkpoint store unavailable (%s: %s); the fixer waits for it", type(e).__name__, e
                )
                return None
            self._stack, self._saver = stack, saver
            return saver

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._saver = None
