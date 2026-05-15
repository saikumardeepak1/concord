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

    # Threshold below which a scoped result is considered "weak" and we expand
    # to an unscoped search. Empirically the right chunks for in-scope queries
    # score 0.55+; anything under 0.45 means the scoped corpus probably doesn't
    # cover this question (usually a router-mis-classification upstream).
    WEAK_SCORE = 0.45

    async def query(
        self,
        *,
        text: str,
        top_k: int | None = None,
        scope: str | None = None,
    ) -> list[RetrievedPassage]:
        """Return top-k passages with a soft scope filter.

        Strategy:
        1. If `scope` is given, run a scoped query first.
        2. If the top scoped result is weak (below WEAK_SCORE) or there are
           fewer than 2 results, expand: run an unscoped query and merge,
           keeping in-scope chunks first then filling slots with the best
           cross-scope chunks until k is reached.

        This preserves the focus benefit of scoping while adding a safety net
        for cases where the router sent the question to the wrong specialist.
        """
        k = top_k or self._settings.retrieval_top_k
        async with span("retrieval.query", k=k, scope=scope) as s:
            scoped = self._raw_query(text, k, scope) if scope else []
            cross_scope_used = False
            if scope is None:
                passages = self._raw_query(text, k, None)
            elif scoped and scoped[0].score >= self.WEAK_SCORE and len(scoped) >= 2:
                passages = scoped
            else:
                # Scoped results are weak or sparse. Expand to unscoped.
                cross_scope_used = True
                unscoped = self._raw_query(text, k, None)
                seen: set[str] = set()
                merged: list[RetrievedPassage] = []
                for p in scoped + unscoped:
                    key = f"{p.doc_id}#{p.chunk_index}"
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(p)
                    if len(merged) >= k:
                        break
                # Sort by score so the strongest cross-scope match wins if it
                # beats the strongest in-scope one (the common router-error case).
                merged.sort(key=lambda x: x.score, reverse=True)
                passages = merged[:k]

            s.attributes["hits"] = len(passages)
            s.attributes["cross_scope_used"] = cross_scope_used
            if passages:
                s.attributes["top_score"] = round(passages[0].score, 3)
            return passages

    def _raw_query(
        self, text: str, k: int, scope: str | None
    ) -> list[RetrievedPassage]:
        """Internal: one Chroma query, optionally scope-filtered."""
        where = {"scope": scope} if scope else None
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
