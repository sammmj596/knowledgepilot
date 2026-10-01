from __future__ import annotations

import boto3
import json
import os

import logging
import time
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from langchain_core.messages import AIMessage, HumanMessage

from app.config import OPENAI_API_KEY
from app.graph import get_rag_app, initial_rag_state
from app.ingestion import upload_document_file
from app.ingestion_jobs import list_ingestion_jobs, get_ingestion_job
from app.mcp_tools import mcp_load_error, mcp_tools_available
from app.semantic_cache import get_cached_response, cache_response

# --- Logging setup -----------------------------------------------------
# Writes to backend/knowledgepilot.log, one line per request, in a fixed
# positional format so it's easy to `parse` in CloudWatch Logs Insights:
#   <ISO timestamp> METHOD /path STATUS LATENCYms
LOG_PATH = Path(__file__).resolve().parent.parent / "knowledgepilot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),  # keep console output too, for local dev
    ],
)
request_logger = logging.getLogger("knowledgepilot.requests")
# ------------------------------------------------------------------------

SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
sqs = boto3.client("sqs", region_name=os.environ.get("SQS_REGION", "us-west-1"))

def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.before_request
    def _start_timer():
        request.start_time = time.time()

    @app.after_request
    def _log_request(response):
        latency_ms = round((time.time() - getattr(request, "start_time", time.time())) * 1000, 2)
        request_logger.info(
            "%s %s %s %sms",
            request.method,
            request.path,
            response.status_code,
            latency_ms,
        )
        return response

    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "openai_configured": bool(OPENAI_API_KEY),
                "mcp_web_search_enabled": mcp_tools_available(),
                "mcp_load_error": mcp_load_error(),
            }
        )

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        messages_in = payload.get("messages")
        if not isinstance(messages_in, list) or not messages_in:
            return jsonify({"error": "messages must be a non-empty list"}), 400
        lc_messages = []
        for m in messages_in:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role == "user" and isinstance(content, str):
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant" and isinstance(content, str):
                lc_messages.append(AIMessage(content=content))
        if not lc_messages or not isinstance(lc_messages[-1], HumanMessage):
            return jsonify({"error": "last message must be from user"}), 400

        question = lc_messages[-1].content

        cached_answer = get_cached_response(question)
        if cached_answer is not None:
            return jsonify({
                "message": {"role": "assistant", "content": cached_answer},
                "cached": True
            })

        try:
            result = get_rag_app().invoke(initial_rag_state(lc_messages))
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        out_msgs = result.get("messages") or []
        last = out_msgs[-1] if out_msgs else None
        if isinstance(last, AIMessage):
            text = last.content if isinstance(last.content, str) else str(last.content)
        else:
            text = ""

        if text:
            cache_response(question, text)
            
        return jsonify({"message": {"role": "assistant", "content": text}})

    @app.post("/api/ingest/file")
    def upload_file_route():
        """Save uploaded file to disk and create an ingestion job (no vector ingest)."""
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "file is required"}), 400
        source = request.form.get("source")
        source = source if isinstance(source, str) and source.strip() else None
        try:
            job = upload_document_file(
                upload.read(),
                filename=upload.filename,
                source=source,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify(_job_upload_response(job, filename=upload.filename)), 201

    @app.post("/api/ingest/jobs/<job_id>/vectorize")
    def vectorize_job_route(job_id: str):
        """Queue a job for async vectorization via SQS."""
        job = get_ingestion_job(job_id)
        if job is None:
            return jsonify({"error": f"Ingestion job not found: {job_id}"}), 404

        try:
            sqs.send_message(
                QueueUrl=SQS_QUEUE_URL,
                MessageBody=json.dumps({"job_id": job_id}),
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        return jsonify(
            {
                "job_id": job["id"],
                "status": "queued",
                "source": job.get("source"),
                "file_path": job.get("file_path"),
            }
        ), 202

    @app.get("/api/ingest/jobs")
    def list_jobs_route():
        status = request.args.get("status")
        if status is not None and not isinstance(status, str):
            return jsonify({"error": "status must be a string"}), 400
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        jobs = list_ingestion_jobs(status=status, limit=max(1, min(limit, 200)))
        return jsonify({"jobs": jobs})

    return app


def _job_upload_response(job: dict, *, filename: str | None = None) -> dict:
    return {
        "job_id": job["id"],
        "status": job["status"],
        "source": job.get("source"),
        "file_path": job.get("file_path"),
        "filename": filename,
    }


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)