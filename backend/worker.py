"""Standalone SQS worker: polls rag-ingestion-queue and runs vectorization jobs."""
from __future__ import annotations

import json
import logging
import os
import signal
import time

import boto3

from app.ingestion import run_vector_ingest
from app.ingestion_jobs import get_ingestion_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

QUEUE_URL = os.environ["SQS_QUEUE_URL"]
REGION = os.environ.get("SQS_REGION", "us-west-1")

sqs = boto3.client("sqs", region_name=REGION)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down after current poll...", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def process_message(message: dict) -> None:
    body = json.loads(message["Body"])
    job_id = body["job_id"]

    job = get_ingestion_job(job_id)
    if job is None:
        logger.warning("Job %s not found, skipping", job_id)
        return
    if job["status"] == "chunked":
        logger.info("Job %s already completed, skipping duplicate", job_id)
        return

    logger.info("Processing job %s", job_id)
    try:
        run_vector_ingest(job_id)
        logger.info("Job %s completed", job_id)
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        raise


def main() -> None:
    logger.info("Worker started, polling %s", QUEUE_URL)
    while not _shutdown:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=300,
            )
        except Exception as exc:
            logger.exception("Failed to poll SQS: %s", exc)
            time.sleep(5)
            continue
        messages = response.get("Messages", [])
        if not messages:
            continue

        for message in messages:
            try:
                process_message(message)
                sqs.delete_message(
                    QueueUrl=QUEUE_URL,
                    ReceiptHandle=message["ReceiptHandle"],
                )
            except Exception:
                # Leave message in queue; it becomes visible again after
                # the visibility timeout and will be retried.
                continue

    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()