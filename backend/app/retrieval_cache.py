from __future__ import annotations

import json 

from langchain_community.vectorstores import OpenSearchVectorSearch
from opensearchpy.exceptions import NotFoundError
from langchain_core.documents import Document

from app.config import OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASSWORD
from app.vector_store import get_embeddings

RETRIEVAL_SIMILARITY_THRESHOLD = 0.85
RETRIEVAL_CACHE_INDEX_NAME = "retrieval_result_cache"

_retrieval_cache_store: OpenSearchVectorSearch | None = None

def get_retrieval_cache_store() -> OpenSearchVectorSearch:
    global _retrieval_cache_store
    if _retrieval_cache_store is None:
        _retrieval_cache_store = OpenSearchVectorSearch(
            opensearch_url=OPENSEARCH_URL,
            index_name=RETRIEVAL_CACHE_INDEX_NAME,
            embedding_function=get_embeddings(),
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
            use_ssl=True,
            verify_certs=True,
            pool_maxsize=10,  # keep connections for parallel sub-question searches
            engine="faiss",
        )
    return _retrieval_cache_store

def get_cached_retrieval(query: str) -> list[Document] | None:
    store = get_retrieval_cache_store()
    try:
        results = store.similarity_search_with_relevance_scores(query, k=1)
    except NotFoundError:
        return None
    
    if not results:
        return None

    doc, score = results[0]
    if score < RETRIEVAL_SIMILARITY_THRESHOLD:
        return None

    raw_chunks = doc.metadata.get("chunks")
    if not raw_chunks:
        return None

    chunk_dicts = json.loads(raw_chunks)
    return [
        Document(page_content=c["page_content"], metadata = c.get("metadata", {}))
        for c in chunk_dicts
    ]

def cache_retrieval(query: str, docs: list[Document]) -> None:
    if not docs:
        return
    store = get_retrieval_cache_store()
    chunk_dicts = [
        {"page_content": d.page_content, "metadata": d.metadata}
        for d in docs
    ]
    store.add_texts(
        texts = [query],
        metadatas=[{"chunks": json.dumps(chunk_dicts)}]
    )
