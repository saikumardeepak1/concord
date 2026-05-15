"""Retrieval service.

Local Chroma collection with a sentence-transformers embedder. Built to be
called either in-process (orchestrator uses `RetrievalService` directly) or
remotely as an MCP server (see `concord.mcp_servers.retrieval_server`).

Indexing is idempotent: re-running `index_knowledge_dir` upserts by chunk-id,
so a poll-on-startup deploy never duplicates the corpus.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions

from concord.config import get_settings
from concord.models import RetrievedPassage
from concord.observability.tracing import span
from concord.retrieval.chunking import chunk_markdown


class RetrievalService:
    """Wraps Chroma to expose a single `query` and `index` surface."""

    COLLECTION = "concord_kb"

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION,
            embedding_function=self._embedder,
            metadata={"hnsw:space": "cosine"},
        )

    def index_knowledge_dir(self, knowledge_dir: str | Path | None = None) -> int:
        """Scan and index every .md under `knowledge_dir`. Returns chunk count."""
        settings = self._settings
        path = Path(knowledge_dir or settings.knowledge_dir)
        if not path.exists():
            return 0

        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict] = []
        for md in sorted(path.rglob("*.md")):
            chunks = chunk_markdown(
                md,
                max_chars=settings.retrieval_chunk_chars,
                overlap=settings.retrieval_chunk_overlap,
            )
            for c in chunks:
                cid = f"{c.doc_id}#{c.chunk_index}"
                ids.append(cid)
                texts.append(c.text)
                metadatas.append(
                    {
                        "doc_id": c.doc_id,
                        "title": c.title,
                        "source": c.source,
                        "chunk_index": c.chunk_index,
                        "heading_trail": c.heading_trail,
                        # Use the directory under knowledge as a scope tag so
                        # specialists can restrict their retrieval (e.g. only
                        # billing docs for the billing specialist).
                        "scope": _scope_from_path(md, path),
                    }
                )
        if not ids:
            return 0
        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
        return len(ids)

    async def query(
        self,
        *,
        text: str,
        top_k: int | None = None,
        scope: str | None = None,
    ) -> list[RetrievedPassage]:
        """Return top-k passages, optionally restricted to a knowledge scope."""
        k = top_k or self._settings.retrieval_top_k
        where = {"scope": scope} if scope else None
        async with span("retrieval.query", k=k, scope=scope) as s:
            # Chroma's API is sync; we treat it as fast enough to call inline.
            result = self._collection.query(
                query_texts=[text],
                n_results=k,
                where=where,
            )
            passages: list[RetrievedPassage] = []
            docs = (result.get("documents") or [[]])[0]
            metas = (result.get("metadatas") or [[]])[0]
            dists = (result.get("distances") or [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists, strict=False):
                # Chroma cosine distance -> similarity in [0, 1] (approx).
                score = max(0.0, 1.0 - float(dist))
                passages.append(
                    RetrievedPassage(
                        doc_id=meta.get("doc_id", ""),
                        title=meta.get("title", ""),
                        text=doc,
                        source=meta.get("source", ""),
                        score=score,
                        chunk_index=int(meta.get("chunk_index", 0)),
                    )
                )
            s.attributes["hits"] = len(passages)
            return passages

    def reset(self) -> None:
        self._client.reset()
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION, embedding_function=self._embedder
        )


def _scope_from_path(file_path: Path, root: Path) -> str:
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return "general"
    parts = rel.parts
    return parts[0] if len(parts) > 1 else "general"


_singleton: RetrievalService | None = None


def get_retrieval_service() -> RetrievalService:
    global _singleton
    if _singleton is None:
        _singleton = RetrievalService()
    return _singleton
