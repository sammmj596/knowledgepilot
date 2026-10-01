"""Re-ingest every indexed document whose chunks lack the "[Source: ...]" prefix.

Run inside the backend container (one job at a time, memory-friendly):
    docker compose cp reingest_all.py backend:/tmp/reingest_all.py
    docker compose exec -w /app backend python /tmp/reingest_all.py            # dry run
    docker compose exec -w /app backend python /tmp/reingest_all.py --run      # do it

For each job: delete its old chunks, then call run_vector_ingest(job_id) directly
(no SQS / worker). If a job fails after its delete, it is left as status
"uploaded" with an error message, and the script tells you so you can retry.
"""
import argparse
import sys

sys.path.insert(0, "/app")

from opensearchpy import helpers  # noqa: E402

from app.config import COLLECTION_NAME  # noqa: E402
from app.ingestion import run_vector_ingest  # noqa: E402
from app.vector_store import get_vector_store  # noqa: E402


def find_stale_jobs(client) -> dict[str, dict]:
    """Return {job_id: {"source": str, "chunks": int}} for jobs missing the prefix."""
    jobs: dict[str, dict] = {}
    for hit in helpers.scan(
        client,
        index=COLLECTION_NAME,
        query={"query": {"match_all": {}}, "_source": ["text", "metadata"]},
    ):
        src = hit["_source"]
        meta = src.get("metadata", {})
        job_id = meta.get("job_id")
        if not job_id:
            print(f"  (skipping chunk {hit['_id']}: no job_id, source={meta.get('source')})")
            continue
        info = jobs.setdefault(job_id, {"source": meta.get("source"), "chunks": 0, "stale": False})
        info["chunks"] += 1
        if not src.get("text", "").startswith("[Source:"):
            info["stale"] = True
    return {j: i for j, i in jobs.items() if i["stale"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="actually delete and re-ingest")
    args = ap.parse_args()

    client = get_vector_store().client
    stale = find_stale_jobs(client)
    print(f"Jobs needing re-ingest: {len(stale)}")
    for job_id, info in stale.items():
        print(f"  {job_id}  {info['source']}  ({info['chunks']} chunks)")
    if not args.run or not stale:
        print("\nDry run only. Re-run with --run to execute.")
        return

    for job_id, info in stale.items():
        print(f"\n== {info['source']} ({job_id})")
        try:
            res = client.delete_by_query(
                index=COLLECTION_NAME,
                body={"query": {"match_phrase": {"metadata.job_id": job_id}}},
                refresh=True,
            )
            print(f"  deleted {res['deleted']} old chunks")
            updated = run_vector_ingest(job_id)
            print(f"  re-ingested: status={updated.get('status')} chunks={updated.get('chunk_count')}")
        except Exception as exc:  # keep going; report failures at the end
            print(f"  FAILED: {exc}")


if __name__ == "__main__":
    main()
