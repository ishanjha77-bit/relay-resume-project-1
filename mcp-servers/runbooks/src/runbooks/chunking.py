"""Markdown documents into retrieval chunks: one per ``##`` section.

A runbook's sections (Symptoms, Diagnose, Mitigate, ...) are natural retrieval
units: small enough to rank precisely, whole enough to act on. Text before the
first section is the document's "Overview"; a section longer than ``MAX_CHARS``
is split at paragraph boundaries into "<section> (2)", "(3)", ...
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 1_600
OVERVIEW = "Overview"

_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    kind: str
    title: str
    section: str
    position: int
    text: str

    @property
    def checksum(self) -> str:
        payload = json.dumps([self.kind, self.title, self.section, self.text])
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    @property
    def embedding_text(self) -> str:
        return embedding_text(self.title, self.section, self.text)


def embedding_text(title: str, section: str, text: str) -> str:
    """What gets embedded: the passage under its document and section headings,
    so a terse step ("check pending connections") keeps the context it belongs to."""
    return f"{title} — {section}\n\n{text}"


def parse(markdown: str, doc_id: str, kind: str, default_title: str) -> list[Chunk]:
    title = default_title
    sections: list[tuple[str, list[str]]] = [(OVERVIEW, [])]
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading and heading.group(1) == "#" and title == default_title:
            title = heading.group(2)
        elif heading and heading.group(1) == "##":
            sections.append((heading.group(2), []))
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    for name, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for part in _split(body):
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name} ({seen[name]})"
            chunks.append(Chunk(doc_id, kind, title, label, len(chunks), part))
    return chunks


def _split(body: str) -> Iterator[str]:
    """Pack paragraphs into parts of at most MAX_CHARS (a longer paragraph stays whole)."""
    part: list[str] = []
    size = 0
    for paragraph in re.split(r"\n\s*\n", body):
        if part and size + len(paragraph) > MAX_CHARS:
            yield "\n\n".join(part)
            part, size = [], 0
        part.append(paragraph.strip())
        size += len(paragraph) + 2
    if part:
        yield "\n\n".join(part)


def load_runbooks(directory: Path) -> list[Chunk]:
    """Every ``*.md`` in ``directory`` (README.md excepted), as ``runbook:<file name>``."""
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8")
        chunks.extend(parse(text, f"runbook:{path.stem}", "runbook", path.stem.replace("-", " ")))
    return chunks
