# KnowledgePilot

A production RAG + agent system on AWS. Upload documents, then ask questions and get streamed answers grounded in them.

**Live demo:** https://knowledgepilot.dev

I took a local RAG prototype and turned it into a deployed system: cloud infrastructure, async document ingestion, two-tier semantic caching, hybrid retrieval, parallel query execution, and streamed responses.

## Features

- **Hybrid retrieval:** dense vector search and BM25 keyword search, merged with reciprocal rank fusion.
- **Agentic answer pipeline (LangGraph):** a planner splits complex questions into sub-questions, which run in parallel. A separate LLM-as-judge step validates each answer, and a refine loop corrects answers that fail.
- **Streaming responses:** answers appear token by token over Server-Sent Events, with live progress status ("Searching documents…", "Checking answer…").
- **Two-tier semantic caching:** final answers and retrieved chunk sets are cached by query similarity. Repeated questions return in about half a second.
- **Async document ingestion:** uploads go to S3, and a separate worker chunks, embeds, and indexes them through an SQS queue, so the web server never blocks on embedding.
- **Supports** PDF (text-based), DOCX, TXT, Markdown, HTML, CSV, and JSON uploads.

## Architecture

```
Browser ──HTTPS──> Nginx (static React build + reverse proxy)
                      │
                      └── /api/* ──> Flask + Gunicorn (EC2)
                                        │
        ┌───────────────┬───────────────┼────────────────┬──────────────┐
        ▼               ▼               ▼                ▼              ▼
   Semantic        LangGraph        MySQL (RDS)      S3 (files)    SQS queue
 response cache   RAG pipeline    ingestion jobs                       │
 (OpenSearch)         │                                                ▼
                      ▼                                        Worker container
              Retrieval cache ──miss──> Hybrid search          chunk → embed →
              (OpenSearch)              (BM25 + vectors,       write to OpenSearch
                                         OpenSearch)
                      │
                      └── embeddings cached in ElastiCache (Redis)
```

**Chat flow:** semantic response cache → LangGraph pipeline (`plan` → `retrieve`, or parallel sub-questions → `generate` / `merge` → `validate` → `refine`, up to a retry limit) → streamed to the browser.

**Ingestion flow:** upload to S3 → job row in MySQL → message on SQS → worker container chunks, embeds, and indexes the document. Jobs are idempotent, and a job stuck mid-processing is detected by its `updated_at` timestamp and retried.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask, Gunicorn, LangChain, LangGraph |
| Frontend | React, TypeScript, Vite |
| Models | OpenAI (embeddings and LLM) |
| Search | Amazon OpenSearch Service (k-NN vectors and BM25) |
| Database | MySQL on Amazon RDS |
| Cache | Amazon ElastiCache for Redis |
| Storage / queue | Amazon S3, Amazon SQS |
| Infrastructure | EC2, Docker Compose, Nginx, Let's Encrypt (TLS) |

## Engineering highlights

### Hybrid search fixed a real retrieval failure
A question about "CS 32 Final" returned only unrelated documents, even though the file was indexed. Inspecting the raw chunks showed the course name existed only in the filename metadata. I prepended a `[Source: …]` line to every chunk before embedding, and added BM25 over the chunk text plus the source field (boosted 3x) to the retrieval path. Dense and keyword rankings are combined with reciprocal rank fusion. The existing documents were then re-ingested.

### Parallel sub-questions with a bounded thread pool
Sub-questions are independent and spend nearly all their time waiting on OpenAI and OpenSearch, so they run on a shared `ThreadPoolExecutor` (one pool per process, shared by all request threads). That caps total concurrency no matter how many users are active. The OpenSearch clients keep a connection pool sized for it.

### Streaming through a multi-step agent graph
Only the nodes that write the user-visible answer (`generate`, `merge`, `refine`) are streamed. Internal LLM calls (planning, validation, sub-answers) are filtered out. If validation fails and the answer is refined, the client receives a `replace` event and swaps in the corrected text. Nginx buffering is disabled for `/api/`, and the non-streaming endpoint remains as a fallback.

### Resource-constrained production
The app runs on a 1 GB `t3.micro`. Out-of-memory worker kills were reduced by moving Redis to ElastiCache, adding swap, and running a single Gunicorn worker with threads and a 120 s timeout (matched by Nginx's read timeout).

## Performance (single runs on the live site)

| Request | Time |
|---|---|
| Simple question, uncached | ~12.5 s |
| Same question, semantic cache hit | ~0.5 s |
| Multi-part question, sequential sub-questions | ~30 s |
| Same multi-part question, parallel sub-questions | ~15 s |

These are individual measurements from request logs, not a benchmark. Streaming does not shorten the total time, but the first status update and text appear much sooner.

## Other problems debugged

- **Worker retried healthy jobs forever.** A variable was assigned only inside one branch of the stuck-job check, so jobs in any other state crashed the worker on every attempt.
- **413 errors on larger PDFs.** Nginx's default 1 MB upload limit; raised to 20 MB.
- **S3 `AccessDenied` after an IAM role change.** Traced to the instance role and its attached policies.
- **Upload status never updated on its own.** The backend returns `queued` but only stores `uploaded` until the worker starts, so the page's polling condition never became true. The UI now tracks jobs it submitted until they finish.

## Running locally

The app depends on AWS services and cannot run fully offline. You need:

- an OpenAI API key
- an OpenSearch domain
- a MySQL database
- an S3 bucket and an SQS queue
- a Redis instance (optional)

```bash
# backend
cd backend
cp .env.example .env        # fill in the variables below
pip install -r requirements.txt
python run.py               # API on http://localhost:5050
python worker.py            # ingestion worker, in a second terminal

# frontend
cd frontend
npm install
npm run dev
```

Main environment variables:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | embeddings and LLM |
| `OPENSEARCH_URL`, `OPENSEARCH_USER`, `OPENSEARCH_PASSWORD` | vector and keyword search |
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` | ingestion job tracking |
| `S3_BUCKET`, `S3_REGION` | uploaded files |
| `SQS_QUEUE_URL`, `SQS_REGION` | ingestion queue |
| `REDIS_URL` | embeddings cache |
| `RAG_RETRIEVAL_K`, `RAG_MAX_RETRIES`, `RAG_SUB_QUESTION_WORKERS`, `RAG_MAX_SUB_QUESTIONS` | pipeline tuning |

## Known limitations and next steps

- **Single small instance:** one EC2 `t3.micro`, no autoscaling. Next step is ECS/Fargate behind a load balancer.
- **No OCR:** scanned (image-only) PDFs fail text extraction. A Textract or Tesseract fallback is the planned fix.
- **No automated tests:** retrieval quality is checked with a golden-dataset builder, but there is no formal test suite yet.
- **No authentication:** documents and chat are open to anyone with the link, which is fine for a demo and not for production.
