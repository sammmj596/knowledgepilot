from __future__ import annotations

from langchain_community.vectorstores import OpenSearchVectorSearch
from opensearchpy.exceptions import NotFoundError
from app.config import OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASSWORD
from app.vector_store import get_embeddings

SIMILARITY_THRESHOLD = 0.92
CACHE_INDEX_NAME = "semantic_response_cache"

_cache_store: OpenSearchVectorSearch | None = None


def get_cache_store() -> OpenSearchVectorSearch:
    global _cache_store
    if _cache_store is None:
        _cache_store = OpenSearchVectorSearch(
            opensearch_url=OPENSEARCH_URL,
            index_name=CACHE_INDEX_NAME,
            embedding_function=get_embeddings(),
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
            use_ssl=True,
            verify_certs=True,
            pool_maxsize=10,  # keep connections for parallel sub-question searches
            engine="faiss",
        )
    return _cache_store


def get_cached_response(query: str) -> str | None:
    store = get_cache_store()
    try:
        results = store.similarity_search_with_relevance_scores(query, k=1)
    except NotFoundError:
        return None

    if not results:
        return None

    doc, score = results[0]
    if score >= SIMILARITY_THRESHOLD:
        return doc.metadata.get("answer")
    return None


def cache_response(query: str, answer: str, source_doc_ids: list[str] | None = None) -> None:
    store = get_cache_store()
    store.add_texts(
        texts=[query],
        metadatas=[{
            "answer": answer,
            "source_doc_ids": ",".join(source_doc_ids or []),
        }]
    )
