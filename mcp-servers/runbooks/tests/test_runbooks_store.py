"""The store against a real pgvector Postgres (Docker)."""

from pathlib import Path

import psycopg
from kb_support import POOL, TopicEmbedder, add_postmortem, run, vote
from runbooks.chunking import load_runbooks, parse
from runbooks.store import KnowledgeStore, SyncResult


def unembedded(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT section FROM knowledge_chunks WHERE embedding IS NULL ORDER BY id")
        return [r[0] for r in rows]


def test_sync_adds_updates_and_removes_and_keeps_vectors_of_unchanged_sections(
    db: str, runbooks: Path
) -> None:
    embedder = TopicEmbedder()

    async def scenario() -> list[SyncResult]:
        async with KnowledgeStore(db) as store:
            results = [await store.sync("runbook", load_runbooks(runbooks))]
            assert await store.embed_pending(embedder) == 5
            results.append(await store.sync("runbook", load_runbooks(runbooks)))
            (runbooks / "db-pool.md").write_text(POOL.replace("3000ms", "30000ms"), encoding="utf-8")
            (runbooks / "jvm-heap.md").unlink()
            results.append(await store.sync("runbook", load_runbooks(runbooks)))
            return results

    first, again, edited = run(scenario)
    assert first == SyncResult(added=5, updated=0, removed=0, unchanged=0)
    assert again == SyncResult(added=0, updated=0, removed=0, unchanged=5)
    assert edited == SyncResult(added=0, updated=1, removed=3, unchanged=1)
    assert unembedded(db) == ["Symptoms"]  # only the edited section needs a new vector


def test_keywords_find_an_exact_error_message(db: str, runbooks: Path) -> None:
    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(runbooks))
            return await store.search("Connection is not available, request timed out", None, mode="lexical")

    top = run(scenario)[0]
    assert (top.doc_id, top.section, top.matched_by) == ("runbook:db-pool", "Symptoms", ["keywords"])


def test_meaning_finds_a_paraphrase_that_shares_no_words(db: str, runbooks: Path) -> None:
    embedder = TopicEmbedder()

    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(runbooks))
            await store.embed_pending(embedder)
            query = "pods keep getting killed"
            return await store.search(query, embedder.query(query))

    top = run(scenario)[0]
    assert top.doc_id == "runbook:jvm-heap"
    assert top.matched_by == ["meaning"]


def test_hybrid_ranks_passages_both_retrievers_found_first(db: str, runbooks: Path) -> None:
    embedder = TopicEmbedder()

    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(runbooks))
            await store.embed_pending(embedder)
            query = "OutOfMemoryError OOMKilled"
            return await store.search(query, embedder.query(query), limit=3)

    results = run(scenario)
    top = results[0]
    # Only one passage has the exact words; by meaning it is third of the heap passages.
    assert (top.doc_id, top.section, top.matched_by) == (
        "runbook:jvm-heap",
        "Symptoms",
        ["keywords", "meaning"],
    )
    assert (top.lexical_rank, top.semantic_rank) == (1, 3)
    assert abs(top.score - (1 / 61 + 1 / 63)) < 1e-9
    assert all(p.matched_by == ["meaning"] for p in results[1:])


def test_postmortems_the_platform_adds_are_embedded_and_filterable(db: str, runbooks: Path) -> None:
    embedder = TopicEmbedder()
    add_postmortem(db, "INC-7", "Root cause", "A goroutine leak grew memory until the pod was OOMKilled.")

    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(runbooks))  # leaves postmortems alone
            embedded = await store.embed_pending(embedder)
            query = "memory keeps growing"
            only = await store.search(query, embedder.query(query), kind="postmortem")
            listed = await store.documents("postmortem")
            return embedded, only, listed

    embedded, only, listed = run(scenario)
    assert embedded == 6
    assert [p.doc_id for p in only] == ["postmortem:INC-7"]
    assert [d["doc_id"] for d in listed] == ["postmortem:INC-7"]


def ranked(db: str, folder: Path, query: str) -> list[str]:
    embedder = TopicEmbedder()

    async def scenario() -> list[str]:
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(folder))
            await store.embed_pending(embedder)
            return [p.doc_id for p in await store.search(query, embedder.query(query), limit=10)]

    return list(dict.fromkeys(run(scenario)))  # documents, best first


def test_responders_votes_decide_a_close_call(db: str, tmp_path: Path) -> None:
    for name in ("a", "b"):  # two runbooks as relevant as each other
        (tmp_path / f"pool-{name}.md").write_text(
            f"# Pool exhaustion ({name})\n\n## Symptoms\n\nConnections pile up in the pool.\n",
            encoding="utf-8",
        )
    query = "connections pile up"
    assert ranked(db, tmp_path, query) == ["runbook:pool-a", "runbook:pool-b"]  # a tie, broken by name
    vote(db, "runbook:pool-b", 1)
    assert ranked(db, tmp_path, query) == ["runbook:pool-b", "runbook:pool-a"]
    vote(db, "runbook:pool-a", 1, 1, 1, 1)  # four helpful votes count as three
    vote(db, "runbook:pool-b", 1, 1, 1)
    assert ranked(db, tmp_path, query) == ["runbook:pool-a", "runbook:pool-b"]  # 3 against 3 (capped), a tie


def test_votes_never_bury_an_exact_match(db: str, runbooks: Path) -> None:
    vote(db, "runbook:jvm-heap", 1, 1, 1)
    vote(db, "runbook:db-pool", -1, -1, -1)
    exact = "Connection is not available, request timed out after 3000ms"
    assert ranked(db, runbooks, exact)[0] == "runbook:db-pool"


def test_a_query_of_stopwords_finds_nothing_rather_than_failing(db: str, runbooks: Path) -> None:
    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", load_runbooks(runbooks))
            return await store.search("the and of", None)

    assert run(scenario) == []


def test_documents_come_back_in_section_order(db: str) -> None:
    chunks = parse("# T\n\nintro\n\n## B\n\nb\n\n## A\n\na\n", "runbook:t", "runbook", "t")

    async def scenario():
        async with KnowledgeStore(db) as store:
            await store.sync("runbook", chunks)
            return await store.document("runbook:t"), await store.documents()

    document, documents = run(scenario)
    assert [r["section"] for r in document] == ["Overview", "B", "A"]
    assert documents[0]["sections"] == ["Overview", "B", "A"]
