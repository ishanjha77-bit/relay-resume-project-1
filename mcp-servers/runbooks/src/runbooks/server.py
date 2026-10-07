"""MCP server: search Relay's runbooks and the postmortems of past incidents."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from relay_mcp_kit import KitSettings, clip, create_server, redact, serve, to_json

from runbooks.embed import BgeSmall
from runbooks.librarian import Librarian
from runbooks.store import KnowledgeStore

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
MAX_DOCUMENT_CHARS = 12_000
MAX_QUERY_CHARS = 1_000

Kind = Literal["runbook", "postmortem"]

INSTRUCTIONS = """\
Relay's knowledge base: runbooks the team wrote for known failure modes, and
the postmortems of past incidents. `search` finds the passages most relevant to
a symptom, error message, metric or hypothesis; `read_document` returns a whole
runbook or postmortem. This is background knowledge about how things fail and
how to check them, not evidence about the current incident: confirm anything
it suggests with logs, metrics or the cluster before relying on it."""

NOTE = "Background knowledge, not evidence about this incident: confirm with logs, metrics or the cluster."


def bundled_runbooks() -> Path:
    """content/ ships inside the wheel next to this module; in a source checkout
    it is mcp-servers/runbooks/content."""
    here = Path(__file__).resolve().parent
    packaged = here / "content"
    return packaged if packaged.is_dir() else here.parents[1] / "content"


def build_server(
    store: KnowledgeStore,
    librarian: Librarian,
    settings: KitSettings | None = None,
    *,
    background: bool = True,
) -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        async with store, anyio.create_task_group() as tasks:
            if background:
                tasks.start_soon(librarian.run)
            yield
            tasks.cancel_scope.cancel()

    mcp = create_server(
        "runbooks", instructions=INSTRUCTIONS, scopes=["knowledge:read"], settings=settings, lifespan=lifespan
    )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def search(query: str, kind: Kind | None = None, limit: int = 5) -> str:
        """Find runbook and postmortem passages relevant to a symptom, error message,
        metric or hypothesis. Matches exact terms (an error string, a metric name) and
        meaning ("pods keep getting killed" finds OOMKilled), and fuses both rankings.
        Each result names its document, so read_document can fetch the whole thing.

        Args:
            query: What you see or want to check, e.g. "Connection is not available, request timed out after 3000ms" or "payments restarts with OOMKilled".
            kind: Only "runbook" or only "postmortem"; omit for both.
            limit: How many passages to return, 1-10 (default 5).
        """
        query = query.strip()[:MAX_QUERY_CHARS]
        if not query:
            return to_json({"error": "query is empty"})
        embedder = librarian.embedder
        try:
            vector = await anyio.to_thread.run_sync(embedder.query, query) if embedder else None
            passages = await store.search(query, vector, kind=kind, limit=max(1, min(limit, 10)))
        except Exception as e:
            return to_json({"error": f"knowledge base unavailable: {type(e).__name__}"})
        return to_json(
            {
                "query": query,
                "mode": "hybrid" if vector is not None else "keywords only (the embedding model is loading)",
                "results": [
                    {
                        "doc_id": p.doc_id,
                        "kind": p.kind,
                        "title": p.title,
                        "section": p.section,
                        "matched_by": p.matched_by,
                        "score": round(p.score, 4),
                        "text": redact(p.text),
                    }
                    for p in passages
                ],
                "note": NOTE,
            }
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def read_document(doc_id: str) -> str:
        """The full text of one runbook or postmortem, section by section.

        Args:
            doc_id: As returned by search or list_documents, e.g. "runbook:db-connection-pool".
        """
        try:
            rows = await store.document(doc_id.strip())
        except Exception as e:
            return to_json({"error": f"knowledge base unavailable: {type(e).__name__}"})
        if not rows:
            return to_json({"error": f"no document {doc_id!r}; list_documents shows what exists"})
        sections: list[dict[str, Any]] = []
        budget = MAX_DOCUMENT_CHARS
        for row in rows:
            text = clip(redact(row["text"]), max(budget, 0))
            sections.append({"section": row["section"], "text": text})
            budget -= len(text)
            if budget <= 0:
                break
        return to_json(
            {
                "doc_id": doc_id,
                "kind": rows[0]["kind"],
                "title": rows[0]["title"],
                "sections": sections,
                **({"truncated": True} if len(sections) < len(rows) or budget < 0 else {}),
                "note": NOTE,
            }
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def list_documents(kind: Kind | None = None) -> str:
        """Every runbook and postmortem in the knowledge base, with its sections.

        Args:
            kind: Only "runbook" or only "postmortem"; omit for both.
        """
        try:
            rows = await store.documents(kind)
        except Exception as e:
            return to_json({"error": f"knowledge base unavailable: {type(e).__name__}"})
        return to_json(
            {
                "documents": [
                    {
                        "doc_id": r["doc_id"],
                        "kind": r["kind"],
                        "title": r["title"],
                        "sections": r["sections"],
                        "updated": r["updated_at"].date().isoformat(),
                    }
                    for r in rows
                ]
            }
        )

    return mcp


def main() -> None:
    settings = KitSettings.from_env()
    store = KnowledgeStore(os.environ["DATABASE_URL"])
    cache = os.environ.get("EMBEDDING_CACHE_DIR") or None
    librarian = Librarian(
        store,
        lambda: BgeSmall(cache),
        Path(os.environ.get("RUNBOOKS_DIR") or bundled_runbooks()),
        interval_s=float(os.environ.get("EMBED_INTERVAL_S", "10")),
    )
    serve(build_server(store, librarian, settings), settings)


if __name__ == "__main__":
    main()
