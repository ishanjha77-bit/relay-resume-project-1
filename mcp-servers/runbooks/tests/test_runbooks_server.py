"""The MCP tools and the background librarian, against a real pgvector Postgres."""

import json
from pathlib import Path
from typing import Any

from kb_support import TopicEmbedder, add_postmortem, run
from relay_mcp_kit import KitSettings
from runbooks.librarian import Librarian
from runbooks.server import build_server
from runbooks.store import KnowledgeStore


def call_tools(
    db: str, runbooks: Path, calls: list[tuple[str, dict[str, Any]]], *, model: bool = True
) -> list[Any]:
    async def scenario() -> list[Any]:
        async with KnowledgeStore(db) as store:
            librarian = Librarian(store, TopicEmbedder, runbooks)
            await librarian.tick()  # sync the runbooks
            if model:
                librarian.embedder = TopicEmbedder()
                await librarian.tick()  # embed them
            mcp = build_server(store, librarian, KitSettings(token="test"), background=False)
            out = []
            for tool, args in calls:
                result = await mcp.call_tool(tool, args)
                out.append(json.loads(result.content[0].text))
            return out

    return run(scenario)


def test_search_returns_ranked_passages_with_their_documents(db: str, runbooks: Path) -> None:
    [out] = call_tools(db, runbooks, [("search", {"query": "OutOfMemoryError OOMKilled", "limit": 2})])
    assert out["mode"] == "hybrid"
    assert (out["results"][0]["doc_id"], out["results"][0]["section"]) == ("runbook:jvm-heap", "Symptoms")
    assert out["results"][0]["matched_by"] == ["keywords", "meaning"]
    assert len(out["results"]) == 2
    assert "not evidence" in out["note"]


def test_search_falls_back_to_keywords_while_the_model_loads(db: str, runbooks: Path) -> None:
    [out] = call_tools(db, runbooks, [("search", {"query": "Connection is not available"})], model=False)
    assert out["mode"].startswith("keywords only")
    assert out["results"][0]["doc_id"] == "runbook:db-pool"


def test_documents_can_be_listed_and_read_whole(db: str, runbooks: Path) -> None:
    listed, document, missing, empty = call_tools(
        db,
        runbooks,
        [
            ("list_documents", {}),
            ("read_document", {"doc_id": "runbook:jvm-heap"}),
            ("read_document", {"doc_id": "runbook:nope"}),
            ("search", {"query": "   "}),
        ],
    )
    assert [d["doc_id"] for d in listed["documents"]] == ["runbook:db-pool", "runbook:jvm-heap"]
    assert listed["documents"][1]["sections"] == ["Overview", "Symptoms", "Diagnose"]
    assert document["title"] == "JVM heap exhaustion"
    assert [s["section"] for s in document["sections"]] == ["Overview", "Symptoms", "Diagnose"]
    assert "truncated" not in document
    assert "list_documents" in missing["error"]
    assert empty == {"error": "query is empty"}


def test_postmortem_text_is_redacted_on_the_way_out(db: str, runbooks: Path) -> None:
    add_postmortem(db, "INC-9", "Timeline", "The PSP rejected password=hunter22 in the request body.")
    [out] = call_tools(
        db, runbooks, [("search", {"query": "PSP rejected the request", "kind": "postmortem"})]
    )
    assert "hunter22" not in out["results"][0]["text"]
    assert "[REDACTED]" in out["results"][0]["text"]


def test_an_unreachable_database_is_an_error_result_not_a_crash(runbooks: Path) -> None:
    async def scenario() -> list[Any]:
        store = KnowledgeStore("postgresql://relay:x@127.0.0.1:1/relay?connect_timeout=1", timeout_s=1.0)
        async with store:
            librarian = Librarian(store, TopicEmbedder, runbooks)
            await librarian.tick()  # logs, doesn't raise
            mcp = build_server(store, librarian, KitSettings(token="test"), background=False)
            result = await mcp.call_tool("search", {"query": "heap"})
            return [librarian.synced, json.loads(result.content[0].text)]

    synced, out = run(scenario)
    assert synced is False
    assert out["error"].startswith("knowledge base unavailable")


def test_the_librarian_embeds_new_postmortems_on_its_next_pass(db: str, runbooks: Path) -> None:
    embedder = TopicEmbedder()

    async def scenario() -> list[int]:
        async with KnowledgeStore(db) as store:
            librarian = Librarian(store, lambda: embedder, runbooks)
            librarian.embedder = embedder
            await librarian.tick()
            first = embedder.calls
            add_postmortem(db, "INC-12", "Root cause", "Orders leaked connections.")
            await librarian.tick()
            return [first, embedder.calls]

    first, second = run(scenario)
    assert first == 1  # the five runbook sections, one batch
    assert second == 2  # then just the postmortem
