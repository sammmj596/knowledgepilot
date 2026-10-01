"""Inspect indexed chunks for one ingestion job and check for course-identifying text.

Usage (run on EC2 / anywhere that can reach OpenSearch):
    pip install opensearch-py
    export OPENSEARCH_URL=https://<your-domain-endpoint>
    export OPENSEARCH_USER=...        # if using basic auth
    export OPENSEARCH_PASSWORD=...
    python inspect_chunks.py
    python inspect_chunks.py --job-id <id> --pattern "cs\\s*-?\\s*32"

Adjust the client setup to match how vector_store.py connects if you use IAM/SigV4.
"""
from __future__ import annotations

import argparse
import os
import re

from opensearchpy import OpenSearch

INDEX = "production_rag_docs"
DEFAULT_JOB_ID = "6fbe9b89-34b8-4363-8d4f-5d577e57e0d4"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", default=DEFAULT_JOB_ID)
    ap.add_argument("--pattern", default=r"cs\s*-?\s*32")
    ap.add_argument("--preview", type=int, default=3, help="chunks to preview")
    args = ap.parse_args()

    user, pwd = os.getenv("OPENSEARCH_USER"), os.getenv("OPENSEARCH_PASSWORD")
    client = OpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(user, pwd) if user else None,
        use_ssl=True,
        verify_certs=True,
    )

    resp = client.search(
        index=INDEX,
        body={
            "size": 200,
            "_source": ["text", "metadata"],
            "query": {"match_phrase": {"metadata.job_id": args.job_id}},
        },
    )
    hits = [h["_source"] for h in resp["hits"]["hits"]]
    hits.sort(key=lambda s: s.get("metadata", {}).get("chunk_index", 0))
    print(f"Chunks found for job {args.job_id}: {len(hits)}")
    if not hits:
        return

    rx = re.compile(args.pattern, re.IGNORECASE)
    matching = [
        s["metadata"].get("chunk_index") for s in hits if rx.search(s.get("text", ""))
    ]
    print(f"Chunks matching /{args.pattern}/: {len(matching)} of {len(hits)}")
    print(f"Matching chunk indices: {matching}\n")

    for s in hits[: args.preview]:
        idx = s["metadata"].get("chunk_index")
        text = s.get("text", "")
        print(f"--- chunk {idx} ({len(text)} chars) ---")
        print(text[:300].replace("\n", " "), "\n")


if __name__ == "__main__":
    main()
