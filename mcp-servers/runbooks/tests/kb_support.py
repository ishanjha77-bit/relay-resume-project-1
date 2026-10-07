"""Helpers for the knowledge-base tests: an event loop psycopg can use, a
deterministic stand-in for the embedding model, and sample runbooks."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import anyio
import psycopg

# The platform's Flyway scripts for the knowledge base: its chunks, and responders' votes.
MIGRATIONS = [
    Path(__file__).resolve().parents[3] / "apps/platform-api/src/main/resources/db/migration" / name
    for name in ("V3__knowledge.sql", "V8__document_votes.sql")
]
IMAGE = "pgvector/pgvector:0.8.7-pg18"


def run[T](fn: Callable[[], Awaitable[T]]) -> T:
    # psycopg's async driver can't use the Proactor loop that Windows defaults to.
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    return anyio.run(fn, backend_options=options)


# Each topic is one dimension: texts about the same topic point the same way,
# so "pods keep getting killed" lands near "OOMKilled" without sharing a word.
TOPICS: dict[int, tuple[str, ...]] = {
    0: ("memory", "oom", "oomkilled", "heap", "killed"),
    1: ("connection", "connections", "pool", "hikaricp"),
    2: ("deploy", "rollout", "release", "version"),
    3: ("latency", "slow", "timeout"),
}


class TopicEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def passages(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self.query(t) for t in texts]

    def query(self, text: str) -> list[float]:
        words = set(re.findall(r"[a-z]+", text.lower()))
        vector = [0.0] * 384
        for dim, keys in TOPICS.items():
            vector[dim] = float(len(words.intersection(keys)))
        vector[383] = 0.1  # never the zero vector: its cosine distance is undefined
        return vector


JVM = """# JVM heap exhaustion

Java services that run out of heap space.

## Symptoms

OutOfMemoryError: Java heap space in the logs; the container restarts and its
last state is OOMKilled.

## Diagnose

Compare heap used after GC with the max heap.
"""

POOL = """# Database connection pool exhaustion

## Symptoms

HikariPool-1 - Connection is not available, request timed out after 3000ms.

## Mitigate

Restart the service to release leaked connections, then roll back the change
that leaks them.
"""


def vote(url: str, doc_id: str, *votes: int) -> None:
    """Responders' votes on a document, one incident each (what the platform records)."""
    with psycopg.connect(url, autocommit=True) as conn:
        for i, v in enumerate(votes):
            conn.execute(
                "INSERT INTO document_votes (doc_id, incident_id, username, vote) VALUES (%s, gen_random_uuid(), %s, %s)",
                (doc_id, f"responder-{i}", v),
            )


def add_postmortem(url: str, incident: str, section: str, text: str) -> None:
    """What the platform does when a postmortem is published: rows without vectors."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO knowledge_chunks (doc_id, kind, title, section, position, text, checksum)"
            " VALUES (%s, 'postmortem', %s, %s, 0, %s, 'x')",
            (f"postmortem:{incident}", f"{incident}: payments OOMKilled", section, text),
        )
