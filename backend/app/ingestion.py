from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import CHUNK_OVERLAP, CHUNK_SIZE
from app.file_extract import extract_text_from_bytes, save_uploaded_file
from app.ingestion_jobs import (
    create_ingestion_job,
    get_ingestion_job,
    update_ingestion_job,
)
from app.vector_store import get_vector_store
from app.object_store import download_bytes
from datetime import datetime, timedelta, UTC


def ingest_text(raw_text: str, metadata: dict | None = None) -> int:
    """Split text, embed chunks, and store in the vector database."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_text(raw_text.strip())
    if not chunks:
        return 0
    meta = metadata or {}
    label = meta.get("source") or meta.get("filename") or ""
    docs = [
        Document(page_content=f"[Source: {label}]\n{c}" if label else c,
		 metadata={**meta, "chunk_index": i})
        for i, c in enumerate(chunks)
    ]
    store = get_vector_store()
    store.add_documents(docs)
    return len(docs)


def upload_document_file(
    data: bytes,
    *,
    filename: str,
    source: str | None = None,
) -> dict:
    """Save the original file to disk and create an ingestion job (no vector ingest)."""
    if not filename.strip():
        raise ValueError("filename is required")
    if not data:
        raise ValueError("file is empty")

    label = source or filename
    metadata = {"source": label, "filename": filename}

    job = create_ingestion_job(source=label, metadata=metadata, status="uploading")
    job_id = job["id"]

    try:
        relative_path = save_uploaded_file(data, job_id=job_id, filename=filename)
        updated = update_ingestion_job(
            job_id,
            status="uploaded",
            file_path=relative_path,
            source=label,
            clear_error=True,
        )
    except Exception as exc:
        update_ingestion_job(job_id, error_message=str(exc))
        raise

    if updated is None:
        raise RuntimeError(f"Ingestion job disappeared: {job_id}")
    return updated


def run_vector_ingest(job_id: str) -> dict:
    """Read a saved file for a job, extract text, and ingest into the vector database."""
    job = get_ingestion_job(job_id)
    if job is None:
        raise ValueError(f"Ingestion job not found: {job_id}")

    file_path = job.get("file_path")
    if not file_path:
        raise ValueError(f"job {job_id} has no saved file")

    from datetime import datetime, timedelta

    STUCK_THRESHOLD = timedelta(minutes=10)  # longer than your SQS visibility timeout

    updated_at = None
    if job["status"] == "chunking":
        updated_at = job.get("updated_at")
    if updated_at:
        age = datetime.now(UTC).replace(tzinfo=None) - datetime.fromisoformat(updated_at)
        if age < STUCK_THRESHOLD:
            raise ValueError(f"job {job_id} is already being vectorized")
        # else: stale — treat as abandoned, allow reprocessing
    # no updated_at, treat as stale/allow reprocessing

    data = download_bytes(file_path)
    filename = job.get("metadata", {}).get("filename") or file_path.split("/")[-1]
    text = extract_text_from_bytes(data, filename=filename)

    meta = dict(job.get("metadata") or {})
    meta.setdefault("source", job.get("source") or filename)
    meta.setdefault("filename", filename)
    meta.setdefault("file_path", file_path)
    meta.setdefault("job_id", job_id)

    update_ingestion_job(job_id, status="chunking", content=text, clear_error=True)
    try:
        chunk_count = ingest_text(text, metadata=meta)
        updated = update_ingestion_job(
            job_id,
            status="chunked",
            chunk_count=chunk_count,
            clear_error=True,
        )
    except Exception as exc:
        update_ingestion_job(job_id, status="uploaded", error_message=str(exc))
        raise

    if updated is None:
        raise RuntimeError(f"Ingestion job disappeared: {job_id}")
    return updated
