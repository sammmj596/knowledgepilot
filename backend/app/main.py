from __future__ import annotations

import boto3
import json
import os

import logging
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request
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

# --- Streaming helpers -------------------------------------------------
# Only these graph nodes produce text the user should see. plan / validate and
# the per-sub-question LLM calls are internal and must never be streamed.
_ANSWER_NODES = {"generate", "merge", "refine"}

# Status line shown after a node finishes (describes what happens next).
_NEXT_STATUS = {
    "plan": "Searching documents\u2026",
    "execute": "Combining results\u2026",
    "retrieve": "Writing answer\u2026",
    "mcp_tools": "Writing answer\u2026",
    "merge": "Checking answer\u2026",
    "generate": "Checking answer\u2026",
    "refine": "Checking answer\u2026",
}


def _sse(event: dict) -> str:
    """Format one Server-Sent Event."""
    return f"data: {json.dumps(event)}\n\n"


def _chunk_text(chunk) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else b
            for b in content
            if isinstance(b, (dict, str))
        )
    return ""


def _to_lc_messages(messages_in: list) -> list:
    out = []
    for m in messages_in:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role == "user" and isinstance(content, str):
            out.append(HumanMessage(content=content))
        elif role == "assistant" and isinstance(content, str):
            out.append(AIMessage(content=content))
    return out


def _stream_answer(question: str, lc_messages: list):
    """Yield SSE events for one chat request."""
    try:
        cached_answer = get_cached_response(question)
        if cached_answer is not None:
            yield _sse({"type": "done", "text": cached_answer, "cached": True})
            return

        yield _sse({"type": "status", "text": "Planning\u2026"})
        final_text = ""
        last_streamed_node = None

        for mode, data in get_rag_app().stream(
            initial_rag_state(lc_messages), stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                chunk, meta = data
                node = meta.get("langgraph_node")
                if node not in _ANSWER_NODES or getattr(chunk, "tool_call_chunks", None):
                    continue
                text = _chunk_text(chunk)
                if not text:
                    continue
                # A refine pass rewrites the answer: tell the client to start over.
                if node == "refine" and last_streamed_node != "refine":
                    yield _sse({"type": "replace"})
                last_streamed_node = node
                yield _sse({"type": "token", "text": text})
            elif mode == "updates":
                for node, update in (data or {}).items():
                    if node == "mcp_tools":
                        # Anything streamed before a tool call was only a preamble.
                        yield _sse({"type": "replace"})
                        last_streamed_node = None
                    msgs = update.get("messages") if isinstance(update, dict) else None
                    if node in _ANSWER_NODES and msgs:
                        last = msgs[-1]
                        if isinstance(last, AIMessage) and not last.tool_calls:
                            final_text = (
                                last.content if isinstance(last.content, str) else str(last.content)
                            )
                    if node in _NEXT_STATUS:
                        yield _sse({"type": "status", "text": _NEXT_STATUS[node]})

        if final_text:
            cache_response(question, final_text)
        yield _sse({"type": "done", "text": final_text})
    except Exception as e:  # headers are already sent, so report in-band
        yield _sse({"type": "error", "text": str(e)})

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

    @app.post("/api/chat/stream")
    def chat_stream():
        """Same pipeline as /api/chat, but streamed as Server-Sent Events."""
        payload = request.get_json(silent=True) or {}
        messages_in = payload.get("messages")
        if not isinstance(messages_in, list) or not messages_in:
            return jsonify({"error": "messages must be a non-empty list"}), 400
        lc_messages = _to_lc_messages(messages_in)
        if not lc_messages or not isinstance(lc_messages[-1], HumanMessage):
            return jsonify({"error": "last message must be from user"}), 400

        return Response(
            _stream_answer(lc_messages[-1].content, lc_messages),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # tell Nginx not to buffer this response
            },
        )

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