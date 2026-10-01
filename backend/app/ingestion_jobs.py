from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from app.db import db_cursor

JobStatus = Literal["uploading", "uploaded", "chunking", "chunked"]


def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if isinstance(metadata, (bytes, bytearray)):
        metadata = metadata.decode("utf-8")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = None
    return {
        "id": row["id"],
        "status": row["status"],
        "source": row.get("source"),
        "file_path": row.get("file_path"),
        "content": row.get("content"),
        "chunk_count": row.get("chunk_count", 0),
        "error_message": row.get("error_message"),
        "metadata": metadata,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
    }


def create_ingestion_job(
    *,
    source: str | None = None,
    file_path: str | None = None,
    content: str | None = None,
    metadata: dict | None = None,
    status: JobStatus = "uploading",
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    meta_json = json.dumps(metadata) if metadata else None
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_jobs (id, status, source, file_path, content, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (job_id, status, source, file_path, content, meta_json),
        )
    job = get_ingestion_job(job_id)
    if job is None:
        raise RuntimeError(f"Failed to create ingestion job {job_id}")
    return job


def get_ingestion_job(job_id: str) -> dict[str, Any] | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    return _row_to_job(row) if row else None


def list_ingestion_jobs(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ingestion_jobs"
    params: list[Any] = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with db_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def update_ingestion_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    source: str | None = None,
    file_path: str | None = None,
    content: str | None = None,
    chunk_count: int | None = None,
    error_message: str | None = None,
    clear_error: bool = False,
) -> dict[str, Any] | None:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = %s")
        values.append(status)
        if status == "chunked":
            fields.append("completed_at = %s")
            values.append(datetime.utcnow())
    if source is not None:
        fields.append("source = %s")
        values.append(source)
    if file_path is not None:
        fields.append("file_path = %s")
        values.append(file_path)
    if content is not None:
        fields.append("content = %s")
        values.append(content)
    if chunk_count is not None:
        fields.append("chunk_count = %s")
        values.append(chunk_count)
    if error_message is not None:
        fields.append("error_message = %s")
        values.append(error_message)
    elif clear_error:
        fields.append("error_message = NULL")
    if not fields:
        return get_ingestion_job(job_id)
    values.append(job_id)
    sql = f"UPDATE ingestion_jobs SET {', '.join(fields)} WHERE id = %s"
    with db_cursor() as cur:
        cur.execute(sql, values)
    return get_ingestion_job(job_id)
