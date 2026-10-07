"""Keeps the knowledge base current while the server runs.

On startup it loads the embedding model and syncs the bundled runbooks; from
then on it embeds whatever has no vector yet, every few seconds. That covers
edited runbook sections and, above all, postmortems: the platform writes a
resolved incident's postmortem without an embedding, and within one interval it
is searchable, so the next investigation can learn from the last one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import anyio

from runbooks.chunking import load_runbooks
from runbooks.embed import Embedder
from runbooks.store import KnowledgeStore

log = logging.getLogger(__name__)


class Librarian:
    def __init__(
        self,
        store: KnowledgeStore,
        load_embedder: Callable[[], Embedder],
        runbooks_dir: Path,
        *,
        interval_s: float = 10.0,
    ):
        self.store = store
        self.runbooks_dir = runbooks_dir
        self.interval_s = interval_s
        self._load_embedder = load_embedder
        self.embedder: Embedder | None = None  # None until the model has loaded
        self.synced = False
        self._next_load = 0.0

    async def run(self) -> None:
        while True:
            await self.tick()
            if self.embedder is None and await self._load():
                continue  # embed the backlog right away
            await anyio.sleep(self.interval_s)

    async def _load(self) -> bool:
        """Load the model (a first run may download it). Until it loads, search is
        keyword-only; a failed load is retried every few minutes."""
        if anyio.current_time() < self._next_load:
            return False
        try:
            self.embedder = await anyio.to_thread.run_sync(self._load_embedder)
        except Exception as e:
            self._next_load = anyio.current_time() + 300
            log.warning("embedding model unavailable, search is keyword-only: %s: %s", type(e).__name__, e)
            return False
        log.info("embedding model loaded")
        return True

    async def tick(self) -> None:
        """One pass: sync the runbooks (until that has succeeded once), then embed
        everything pending. Errors are logged and retried on the next pass: the
        database may not be up, or not migrated yet, when the server starts."""
        try:
            if not self.synced:
                chunks = load_runbooks(self.runbooks_dir)
                result = await self.store.sync("runbook", chunks)
                self.synced = True
                log.info(
                    "runbooks synced: %d added, %d updated, %d removed, %d unchanged",
                    result.added,
                    result.updated,
                    result.removed,
                    result.unchanged,
                )
            if self.embedder is None:
                return
            embedded = 0
            while count := await self.store.embed_pending(self.embedder):
                embedded += count
            if embedded:
                log.info("embedded %d chunks", embedded)
        except Exception as e:
            log.warning("knowledge base not ready: %s: %s", type(e).__name__, e)
