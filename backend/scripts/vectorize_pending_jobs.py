#!/usr/bin/env python3
"""Vectorize ingestion jobs that are uploaded but not yet chunked."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.ingestion import run_vector_ingest  # noqa: E402
from app.ingestion_jobs import list_ingestion_jobs  # noqa: E402


def find_pending_jobs(*, limit: int) -> list[dict]:
    jobs = list_ingestion_jobs(status="uploaded", limit=limit)
    return [
        job
        for job in jobs
        if job.get("file_path") and int(job.get("chunk_count") or 0) == 0
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest uploaded jobs (no chunks yet) into the vector database."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max number of uploaded jobs to scan (default: 500)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List pending jobs without vectorizing",
    )
    args = parser.parse_args()

    pending = find_pending_jobs(limit=max(1, args.limit))
    if not pending:
        print("No pending uploaded jobs.")
        return 0

    print(f"Found {len(pending)} pending job(s).")
    failed = 0

    for job in pending:
        job_id = job["id"]
        label = job.get("source") or job.get("file_path") or job_id
        if args.dry_run:
            print(f"  [dry-run] would vectorize {job_id} ({label})")
            continue

        print(f"Vectorizing {job_id} ({label})...")
        try:
            result = run_vector_ingest(job_id)
        except Exception as exc:
            failed += 1
            print(f"  failed: {exc}")
            continue

        print(
            f"  ok: status={result['status']} chunks={result.get('chunk_count', 0)}"
        )

    if args.dry_run:
        return 0
    if failed:
        print(f"Done with {failed} failure(s).")
        return 1
    print("All pending jobs vectorized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
