from pathlib import Path

from runbooks.chunking import MAX_CHARS, OVERVIEW, load_runbooks, parse
from runbooks.server import bundled_runbooks


def test_sections_become_chunks_and_the_intro_is_the_overview() -> None:
    chunks = parse(
        "# Redis cache unavailable\n\nInventory caches stock in Redis.\n\n## Symptoms\n\nErrors.\n\n"
        "## Diagnose\n\n### Step one\n\nCheck the pod.\n",
        "runbook:redis",
        "runbook",
        "redis",
    )
    assert [(c.title, c.section, c.position) for c in chunks] == [
        ("Redis cache unavailable", OVERVIEW, 0),
        ("Redis cache unavailable", "Symptoms", 1),
        ("Redis cache unavailable", "Diagnose", 2),
    ]
    assert chunks[2].text == "### Step one\n\nCheck the pod."
    assert chunks[1].embedding_text == "Redis cache unavailable — Symptoms\n\nErrors."


def test_headings_inside_code_fences_are_text() -> None:
    chunks = parse("# T\n\n## Commands\n\n```\n## not a heading\n```\n", "runbook:t", "runbook", "t")
    assert [c.section for c in chunks] == ["Commands"]
    assert "## not a heading" in chunks[0].text


def test_long_sections_split_at_paragraphs_and_repeated_names_stay_unique() -> None:
    paragraph = "word " * 200  # 1,000 characters
    markdown = f"# T\n\n## Notes\n\n{paragraph}\n\n{paragraph}\n\n## Notes\n\nmore\n"
    sections = [c.section for c in parse(markdown, "runbook:t", "runbook", "t")]
    assert sections == ["Notes", "Notes (2)", "Notes (3)"]


def test_the_checksum_tracks_content_only() -> None:
    a = parse("# T\n\n## S\n\none\n", "runbook:t", "runbook", "t")[0]
    b = parse("# T\n\n\n## S\n\none\n\n", "runbook:t", "runbook", "t")[0]
    c = parse("# T\n\n## S\n\ntwo\n", "runbook:t", "runbook", "t")[0]
    assert a.checksum == b.checksum != c.checksum


def test_the_readme_is_not_a_runbook(runbooks: Path) -> None:
    assert {c.doc_id for c in load_runbooks(runbooks)} == {"runbook:jvm-heap", "runbook:db-pool"}


def test_every_bundled_runbook_follows_the_template() -> None:
    chunks = load_runbooks(bundled_runbooks())
    docs: dict[str, list[str]] = {}
    for chunk in chunks:
        docs.setdefault(chunk.doc_id, []).append(chunk.section)
        assert chunk.title != chunk.doc_id.removeprefix("runbook:").replace("-", " "), chunk.doc_id
        assert len(chunk.text) <= MAX_CHARS + 600, (chunk.doc_id, chunk.section)
    assert len(docs) >= 12
    for doc_id, sections in docs.items():
        assert {OVERVIEW, "Symptoms", "Diagnose", "Mitigate"} <= set(sections), doc_id
