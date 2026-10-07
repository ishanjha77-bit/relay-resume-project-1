"""The knowledge base in Postgres: sync, embed, and hybrid search.

Search runs two retrievers inside one SQL statement and fuses their rankings
with Reciprocal Rank Fusion (score = sum of 1 / (60 + rank)):

- lexical: full-text match with any of the query's terms, ranked by cover
  density; it nails exact strings such as an error message or a metric name;
- semantic: cosine distance between bge-small embeddings (pgvector, HNSW);
  it finds "pods keep getting killed" when the runbook says "OOMKilled".

RRF needs no score calibration between the two, which is why it is the usual
default for hybrid search.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import anyio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from runbooks.chunking import Chunk, embedding_text
from runbooks.embed import Embedder, literal

Mode = Literal["hybrid", "lexical", "semantic"]
RRF_K = 60  # the constant from Cormack et al. (2009); damps the head of each ranking
CANDIDATES = 40  # how deep each retriever's ranking goes before fusion
# Responders' votes scale a document's fused score by up to ±VOTE_CAP × VOTE_WEIGHT (±30%):
# enough to reorder close calls, never enough to bury an exact match.
VOTE_CAP = 3
VOTE_WEIGHT = 0.1

_SEARCH = """
WITH q AS (
    -- Any of the query's terms: plainto_tsquery ANDs them, and a long query
    -- ("orders 5xx after deploy, pool timeouts") should not need every word.
    SELECT nullif(replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | '), '')::tsquery AS query
),
lexical AS (
    SELECT id, row_number() OVER (ORDER BY rank DESC, id) AS rank
    FROM (
        SELECT c.id, ts_rank_cd(c.tsv, q.query, 1) AS rank
        FROM knowledge_chunks c, q
        WHERE %(lexical)s AND c.tsv @@ q.query AND (%(kind)s::text IS NULL OR c.kind = %(kind)s)
        ORDER BY rank DESC
        LIMIT %(candidates)s
    ) matched
),
semantic AS (
    SELECT id, row_number() OVER (ORDER BY distance, id) AS rank
    FROM (
        SELECT c.id, c.embedding <=> %(vector)s::vector AS distance
        FROM knowledge_chunks c
        WHERE %(semantic)s AND c.embedding IS NOT NULL AND (%(kind)s::text IS NULL OR c.kind = %(kind)s)
        ORDER BY distance
        LIMIT %(candidates)s
    ) nearest
),
-- Responders' votes (helpful +1, not -1), capped: feedback re-ranks, it doesn't decide.
votes AS (
    SELECT doc_id, greatest(-%(vote_cap)s, least(%(vote_cap)s, sum(vote))) AS net
    FROM document_votes
    GROUP BY doc_id
)
SELECT c.doc_id, c.kind, c.title, c.section, c.text,
       l.rank AS lexical_rank, s.rank AS semantic_rank, coalesce(v.net, 0) AS votes,
       (coalesce(1.0 / (%(k)s + l.rank), 0) + coalesce(1.0 / (%(k)s + s.rank), 0))
           * (1 + %(vote_weight)s * coalesce(v.net, 0)) AS score
FROM lexical l
FULL JOIN semantic s ON s.id = l.id
JOIN knowledge_chunks c ON c.id = coalesce(l.id, s.id)
LEFT JOIN votes v ON v.doc_id = c.doc_id
ORDER BY score DESC, c.doc_id, c.position
LIMIT %(limit)s
"""

_UPSERT = """
INSERT INTO knowledge_chunks (doc_id, kind, title, section, position, text, checksum)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (doc_id, section) DO UPDATE
SET kind = excluded.kind, title = excluded.title, position = excluded.position,
    text = excluded.text, checksum = excluded.checksum, updated_at = now(),
    embedding = CASE WHEN knowledge_chunks.checksum = excluded.checksum
                     THEN knowledge_chunks.embedding END
"""


@dataclass(frozen=True)
class Passage:
    doc_id: str
    kind: str
    title: str
    section: str
    text: str
    score: float
    lexical_rank: int | None
    semantic_rank: int | None
    votes: int = 0

    @property
    def matched_by(self) -> list[str]:
        return [
            name for name, rank in (("keywords", self.lexical_rank), ("meaning", self.semantic_rank)) if rank
        ]


@dataclass(frozen=True)
class SyncResult:
    added: int
    updated: int
    removed: int
    unchanged: int


class KnowledgeStore:
    def __init__(self, conninfo: str, *, max_size: int = 4, timeout_s: float = 10.0):
        self.pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=max_size,
            timeout=timeout_s,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def open(self) -> None:
        # Don't wait for a connection: the database (or its migration) may not be
        # ready yet, and the pool keeps retrying in the background.
        await self.pool.open(wait=False)

    async def close(self) -> None:
        await self.pool.close()

    async def __aenter__(self) -> KnowledgeStore:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def sync(self, kind: str, chunks: Sequence[Chunk]) -> SyncResult:
        """Make the stored documents of ``kind`` exactly ``chunks``. Unchanged
        sections keep their embeddings; changed and new ones are queued for one."""
        async with self.pool.connection() as conn, conn.transaction():
            # Replicas starting together would otherwise race on the same rows.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('relay.knowledge.sync'))")
            rows = await conn.execute(
                "SELECT doc_id, section, position, checksum FROM knowledge_chunks WHERE kind = %s", (kind,)
            )
            current = {
                (r["doc_id"], r["section"]): (r["position"], r["checksum"]) for r in await rows.fetchall()
            }
            wanted = {(c.doc_id, c.section): c for c in chunks}
            changed = [c for key, c in wanted.items() if current.get(key) != (c.position, c.checksum)]
            stale = [key for key in current if key not in wanted]
            async with conn.cursor() as cur:
                if changed:
                    await cur.executemany(
                        _UPSERT,
                        [
                            (c.doc_id, c.kind, c.title, c.section, c.position, c.text, c.checksum)
                            for c in changed
                        ],
                    )
                if stale:
                    await cur.executemany(
                        "DELETE FROM knowledge_chunks WHERE doc_id = %s AND section = %s", stale
                    )
        updated = sum(1 for c in changed if (c.doc_id, c.section) in current)
        return SyncResult(
            added=len(changed) - updated,
            updated=updated,
            removed=len(stale),
            unchanged=len(wanted) - len(changed),
        )

    async def embed_pending(self, embedder: Embedder, batch: int = 32) -> int:
        """Embed up to ``batch`` chunks that have no vector yet; returns how many.
        Rows are locked with SKIP LOCKED, so several replicas can share the work."""
        async with self.pool.connection() as conn, conn.transaction():
            result = await conn.execute(
                "SELECT id, title, section, text FROM knowledge_chunks WHERE embedding IS NULL"
                " ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
                (batch,),
            )
            rows = await result.fetchall()
            if not rows:
                return 0
            texts = [embedding_text(r["title"], r["section"], r["text"]) for r in rows]
            vectors = await anyio.to_thread.run_sync(embedder.passages, texts)
            async with conn.cursor() as cur:
                await cur.executemany(
                    "UPDATE knowledge_chunks SET embedding = %s::vector WHERE id = %s",
                    [(literal(v), r["id"]) for v, r in zip(vectors, rows, strict=True)],
                )
        return len(rows)

    async def search(
        self,
        text: str,
        vector: Sequence[float] | None,
        *,
        kind: str | None = None,
        limit: int = 5,
        mode: Mode = "hybrid",
    ) -> list[Passage]:
        """Top ``limit`` chunks for ``text``. Without a query vector (the model is
        still loading) the search is lexical only."""
        params: dict[str, Any] = {
            "text": text,
            "vector": literal(vector) if vector is not None else None,
            "kind": kind,
            "lexical": mode in ("hybrid", "lexical"),
            "semantic": mode in ("hybrid", "semantic") and vector is not None,
            "candidates": CANDIDATES,
            "k": RRF_K,
            "limit": limit,
            "vote_cap": VOTE_CAP,
            "vote_weight": VOTE_WEIGHT,
        }
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(_SEARCH, params)).fetchall()
        return [
            Passage(
                doc_id=r["doc_id"],
                kind=r["kind"],
                title=r["title"],
                section=r["section"],
                text=r["text"],
                score=float(r["score"]),
                lexical_rank=r["lexical_rank"],
                semantic_rank=r["semantic_rank"],
                votes=int(r["votes"]),
            )
            for r in rows
        ]

    async def document(self, doc_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                "SELECT kind, title, section, text FROM knowledge_chunks WHERE doc_id = %s ORDER BY position",
                (doc_id,),
            )
            return await result.fetchall()

    async def documents(self, kind: str | None = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                "SELECT doc_id, kind, min(title) AS title, array_agg(section ORDER BY position) AS sections,"
                " count(*) FILTER (WHERE embedding IS NULL) AS unembedded, max(updated_at) AS updated_at"
                " FROM knowledge_chunks WHERE (%(kind)s::text IS NULL OR kind = %(kind)s)"
                " GROUP BY doc_id, kind ORDER BY kind DESC, doc_id",
                {"kind": kind},
            )
            return await result.fetchall()
