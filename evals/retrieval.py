"""Retrieval quality of the runbooks knowledge base.

Does search put the runbook that answers a query at the top? Runs the labelled
queries in evals/retrieval/queries.yaml against a throwaway pgvector container
holding the real runbooks, embedded with the real model, and compares keyword
search, vector search and their fusion. No LLM, no API key, no quota.

    uv run python evals/retrieval.py            # or: make eval-retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
import psycopg
import yaml
from runbooks.chunking import load_runbooks
from runbooks.embed import MODEL, BgeSmall
from runbooks.server import bundled_runbooks
from runbooks.store import RRF_K, KnowledgeStore, Mode

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "evals/retrieval/queries.yaml"
REPORT = ROOT / "evals/reports/retrieval.md"
# The knowledge base as the platform migrates it: chunks, and (empty) responders' votes.
MIGRATIONS = [
    ROOT / "apps/platform-api/src/main/resources/db/migration" / name
    for name in ("V3__knowledge.sql", "V8__document_votes.sql")
]
IMAGE = "pgvector/pgvector:0.8.7-pg18"
DEPTH = 10


@dataclass(frozen=True)
class Query:
    text: str
    expect: frozenset[str]
    style: str


@dataclass(frozen=True)
class Retriever:
    name: str
    mode: Mode
    instruction: bool = False


RETRIEVERS = [
    Retriever("Keywords (Postgres full text)", "lexical"),
    Retriever("Vectors (bge-small)", "semantic"),
    Retriever("Vectors, with BGE's query instruction", "semantic", instruction=True),
    Retriever("Hybrid (RRF of both)", "hybrid"),
    Retriever("Hybrid, with BGE's query instruction", "hybrid", instruction=True),
]


@dataclass
class Outcome:
    query: Query
    rank: int | None  # 1-based rank of the first chunk from an expected runbook
    top: list[str]
    ms: float


def run[T](fn: Callable[[], Awaitable[T]]) -> T:
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    return anyio.run(fn, backend_options=options)


@contextmanager
def database(url: str | None) -> Iterator[str]:
    if url:
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(IMAGE, driver=None) as container:
        url = container.get_connection_url()
        with psycopg.connect(url, autocommit=True) as conn:
            for migration in MIGRATIONS:
                conn.execute(migration.read_text(encoding="utf-8"))
        yield url


def load_queries(path: Path) -> list[Query]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]
    return [Query(q["q"], frozenset(f"runbook:{e}" for e in q["expect"]), q["style"]) for q in raw]


def evaluate(url: str, queries: list[Query]) -> tuple[dict[str, list[Outcome]], dict[str, float]]:
    model = BgeSmall(query_instruction=True)
    chunks = load_runbooks(bundled_runbooks())

    async def scenario() -> tuple[dict[str, list[Outcome]], dict[str, float]]:
        async with KnowledgeStore(url) as store:
            await store.sync("runbook", chunks)
            started = time.perf_counter()
            while await store.embed_pending(model):
                pass
            index_s = time.perf_counter() - started
            embed_ms: list[float] = []
            vectors: dict[tuple[str, bool], list[float]] = {}
            for q in queries:
                started = time.perf_counter()
                vectors[q.text, True] = model.query(q.text)
                embed_ms.append((time.perf_counter() - started) * 1000)
                vectors[q.text, False] = model.passages([q.text])[0]
            results: dict[str, list[Outcome]] = {}
            for retriever in RETRIEVERS:
                outcomes = []
                for q in queries:
                    vector = vectors[q.text, retriever.instruction]
                    started = time.perf_counter()
                    found = await store.search(q.text, vector, limit=DEPTH, mode=retriever.mode)
                    ms = (time.perf_counter() - started) * 1000
                    docs = [p.doc_id for p in found]
                    rank = next((i for i, d in enumerate(docs, 1) if d in q.expect), None)
                    outcomes.append(Outcome(q, rank, docs[:3], ms))
                results[retriever.name] = outcomes
            timing = {
                "chunks": len(chunks),
                "index_s": index_s,
                "embed_query_p50_ms": statistics.median(embed_ms),
            }
            return results, timing

    return run(scenario)


def recall(outcomes: list[Outcome], k: int) -> float:
    return sum(1 for o in outcomes if o.rank is not None and o.rank <= k) / len(outcomes)


def mrr(outcomes: list[Outcome]) -> float:
    return sum(1 / o.rank for o in outcomes if o.rank) / len(outcomes)


def report(queries: list[Query], results: dict[str, list[Outcome]], timing: dict[str, float]) -> str:
    docs = len({c.doc_id for c in load_runbooks(bundled_runbooks())})
    lines = [
        "# Runbook retrieval",
        "",
        f"{len(queries)} labelled queries against {docs} runbooks ({int(timing['chunks'])} chunks).",
        f"Model {MODEL} (384 dimensions, ONNX Runtime on CPU), Postgres 18 with pgvector 0.8.7,",
        f"Reciprocal Rank Fusion with k={RRF_K}. A query is a hit at k when a chunk of a runbook that",
        "answers it is among the first k results. Regenerate with `make eval-retrieval`.",
        "",
        "| Retriever | Recall@1 | Recall@3 | MRR@10 | Search p50 |",
        "|---|---|---|---|---|",
    ]
    for name, outcomes in results.items():
        p50 = statistics.median(o.ms for o in outcomes)
        lines.append(
            f"| {name} | {recall(outcomes, 1):.0%} | {recall(outcomes, 3):.0%} | {mrr(outcomes):.3f} | {p50:.1f} ms |"
        )
    lines += [
        "",
        f"Indexing embedded every chunk in {timing['index_s']:.1f} s; embedding one query takes "
        f"{timing['embed_query_p50_ms']:.1f} ms (p50), on one CPU thread.",
        "",
        "## Recall@3 by query style",
        "",
        "| Style | Queries | " + " | ".join(r.name for r in RETRIEVERS) + " |",
        "|---|---|" + "---|" * len(RETRIEVERS),
    ]
    for style in sorted({q.style for q in queries}):
        cells = [
            f"{recall([o for o in results[r.name] if o.query.style == style], 3):.0%}" for r in RETRIEVERS
        ]
        count = sum(1 for q in queries if q.style == style)
        lines.append(f"| {style} | {count} | " + " | ".join(cells) + " |")
    best = max(results, key=lambda name: (mrr(results[name]), recall(results[name], 3)))
    misses = [o for o in results[best] if o.rank is None or o.rank > 3]
    lines += ["", f"## Misses of the best retriever ({best}): not in the top 3", ""]
    if not misses:
        lines.append("None.")
    for o in misses:
        expected = ", ".join(sorted(e.removeprefix("runbook:") for e in o.query.expect))
        got = ", ".join(d.removeprefix("runbook:") for d in o.top)
        lines.append(
            f"- “{o.query.text}” ({o.query.style}): wanted {expected}; got {got} (rank {o.rank or '>10'})"
        )
    lines += [
        "",
        "## Limits",
        "",
        "- The same person wrote the runbooks and the queries, so the wording overlaps more than it",
        "  would with real responders. The log-style queries reuse the services' real log formats.",
        "- 17 runbooks is a small corpus: rankings get harder as postmortems accumulate.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database", help="Postgres URL with the knowledge schema; default: a throwaway container"
    )
    parser.add_argument("--queries", type=Path, default=QUERIES)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    queries = load_queries(args.queries)
    with database(args.database) as url:
        results, timing = evaluate(url, queries)
    text = report(queries, results, timing)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")
    summary = {
        name: {"recall_at_1": recall(o, 1), "recall_at_3": recall(o, 3), "mrr_at_10": mrr(o)}
        for name, o in results.items()
    }
    args.report.with_suffix(".json").write_text(
        json.dumps({"queries": len(queries), **timing, "retrievers": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(text)


if __name__ == "__main__":
    main()
