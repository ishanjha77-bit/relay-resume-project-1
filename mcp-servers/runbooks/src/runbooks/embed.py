"""Text embeddings, computed locally: no API key, no quota, no per-call cost."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

MODEL = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384  # must match knowledge_chunks.embedding


class Embedder(Protocol):
    def passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class BgeSmall:
    """BAAI/bge-small-en-v1.5 on ONNX Runtime (via fastembed): 33M parameters,
    384 dimensions, a few milliseconds per query on one CPU core.

    ``query_instruction`` is the prefix BGE recommends for short queries against
    longer passages; evals/retrieval measures whether it helps on our corpus.
    """

    QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self, cache_dir: str | None = None, *, query_instruction: bool = True):
        import onnxruntime
        from fastembed import TextEmbedding  # heavy (onnxruntime); kept off the import path of tests

        # Errors only: in a read-only container ORT warns, in plain text, that it
        # can't persist a telemetry ID.
        onnxruntime.set_default_logger_severity(3)
        self._model = TextEmbedding(MODEL, cache_dir=cache_dir, threads=1)
        self._prefix = self.QUERY_INSTRUCTION if query_instruction else ""

    def passages(self, texts: Sequence[str]) -> list[list[float]]:
        # Small batches: ONNX Runtime keeps its peak working memory, and indexing
        # runs in the background, so throughput matters less than footprint.
        return [vector.tolist() for vector in self._model.embed(list(texts), batch_size=4)]

    def query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([self._prefix + text]))).tolist()


def literal(vector: Sequence[float]) -> str:
    """A vector in pgvector's text form, for a ``%s::vector`` parameter."""
    return "[" + ",".join(f"{x:.6g}" for x in vector) + "]"


if __name__ == "__main__":
    # python -m runbooks.embed <dir>: fetch the model ahead of time (image builds),
    # so the server starts with no network access (HF_HUB_OFFLINE=1).
    import sys

    BgeSmall(sys.argv[1] if len(sys.argv) > 1 else None).query("warm up")
