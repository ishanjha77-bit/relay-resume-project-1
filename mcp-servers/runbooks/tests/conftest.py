"""Fixtures: a real Postgres with pgvector (the image the cluster runs), migrated
with the platform's own Flyway script, and a directory of sample runbooks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from kb_support import IMAGE, JVM, MIGRATIONS, POOL


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(IMAGE, driver=None)
    try:
        container.start()
    except Exception as e:  # no Docker: these are integration tests
        pytest.skip(f"needs Docker for {IMAGE}: {e}")
    try:
        url = container.get_connection_url()
        with psycopg.connect(url, autocommit=True) as conn:
            for migration in MIGRATIONS:
                conn.execute(migration.read_text(encoding="utf-8"))
        yield url
    finally:
        container.stop()


@pytest.fixture
def db(database: str) -> str:
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("TRUNCATE knowledge_chunks, document_votes RESTART IDENTITY")
    return database


@pytest.fixture
def runbooks(tmp_path: Path) -> Path:
    (tmp_path / "jvm-heap.md").write_text(JVM, encoding="utf-8")
    (tmp_path / "db-pool.md").write_text(POOL, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Not a runbook\n", encoding="utf-8")
    return tmp_path
