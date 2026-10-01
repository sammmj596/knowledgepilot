from __future__ import annotations

import os
from threading import Lock
from typing import Any, cast

from langchain_community.vectorstores import OpenSearchVectorSearch
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_classic.embeddings import CacheBackedEmbeddings
from langchain_community.storage import RedisStore

from app.config import OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASSWORD, COLLECTION_NAME, OPENAI_API_KEY, REDIS_URL


_lock = Lock()
_store: OpenSearchVectorSearch | None = None


def get_embeddings() -> OpenAIEmbeddings:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    
    underlying = OpenAIEmbeddings(model = "text-embedding-3-small")
    store = RedisStore(redis_url = REDIS_URL)
    return CacheBackedEmbeddings.from_bytes_store(underlying, store, namespace = "text-embedding-3-small")


def get_vector_store() -> OpenSearchVectorSearch:
    global _store
    with _lock:
        if _store is None:
            _store = OpenSearchVectorSearch(
                opensearch_url=OPENSEARCH_URL,
                index_name=COLLECTION_NAME,
                embedding_function=get_embeddings(),
                http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
                use_ssl=True,
                verify_certs=True,
                pool_maxsize=10,  # keep connections for parallel sub-question searches
                engine="faiss",
            )
        return _store


def get_store_stats(*, source: str | None = None, limit: int = 5) -> dict:
    store = get_vector_store()
    client = store.client
    count = client.count(index=COLLECTION_NAME)["count"]

    query: dict[str, Any] = {"query": {"match_all": {}}, "size": limit}
    if source:
        query["query"] = {"match": {"metadata.source": source}}

    result = client.search(index=COLLECTION_NAME, body=query)
    hits = result["hits"]["hits"]

    sources: dict[str, int] = {}
    samples: list[dict] = []
    for hit in hits:
        meta = hit["_source"].get("metadata", {})
        src = str(meta.get("source") or "(none)")
        sources[src] = sources.get(src, 0) + 1
        text = hit["_source"].get("text", "")
        preview = text[:200] + "..." if len(text) > 200 else text
        samples.append({
            "id": hit["_id"],
            "source": meta.get("source"),
            "chunk_index": meta.get("chunk_index"),
            "preview": preview,
        })

    return {
        "collection": COLLECTION_NAME,
        "persist_dir": OPENSEARCH_URL,
        "count": count,
        "sources": sources,
        "samples": samples,
    }


def hybrid_search(query: str, k: int = 10, fetch_k: int | None = None) -> list[Document]:
    """Vector + BM25 search, merged with reciprocal rank fusion."""
    store = get_vector_store()
    fetch_k = fetch_k or k * 3
    vec_docs = store.similarity_search(query, k=fetch_k)
    try:
        resp = store.client.search(
            index=COLLECTION_NAME,
            body={"size": fetch_k, "_source": ["text", "metadata"],
                  "query": {"multi_match": {"query": query, "type": "most_fields",
                          "fields": ["text", "metadata.source^3"]}}},
        )
        bm25_docs = [
            Document(page_content=h["_source"].get("text", ""),
                     metadata=h["_source"].get("metadata", {}))
            for h in resp["hits"]["hits"]
        ]
    except Exception as exc:
        print(f"DEBUG: BM25 failed, vector only: {exc}", flush=True)
        bm25_docs = []

    scores: dict[str, float] = {}
    docs: dict[str, Document] = {}
    for ranked in (vec_docs, bm25_docs):
        for rank, d in enumerate(ranked):
            docs[d.page_content] = d
            scores[d.page_content] = scores.get(d.page_content, 0.0) + 1.0 / (60 + rank + 1)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    return [docs[key] for key in top]
