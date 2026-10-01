# KnowledgePilot

A production RAG + agent system, deployed on AWS: upload documents, then ask questions and get answers grounded in them.

**Live:** https://knowledgepilot.dev

I took a local RAG prototype and turned it into a deployed system: cloud infrastructure, async document ingestion, two-tier semantic caching, and hybrid retrieval.

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

**Chat flow:** semantic response cache → LangGraph pipeline (`plan` → `retrieve` / sub-questions → `generate` → `validate` → `refine`, up to a retry limit).

**Ingestion flow:** upload to S3 → job row in MySQL → message on SQS → a separate worker container chunks, embeds, and indexes the document. The web workers never block on embedding.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, Flask, Gunicorn, LangChain, LangGraph |
| Frontend | React, TypeScript, Vite |
| Models | OpenAI (embeddings + LLM) |
| Vector / search | Amazon OpenSearch Service |
| Database | MySQL on Amazon RDS |
| Cache | Amazon ElastiCache for Redis |
| Storage / queue | S3, SQS |
| Infra | EC2, Docker Compose, Nginx, Let's Encrypt (TLS) |

## What I built

- **AWS deployment:** EC2, RDS, S3, OpenSearch, SQS, and ElastiCache wired together with scoped IAM roles, HTTPS via Nginx and Certbot, and a custom domain.
- **Async ingestion:** SQS-backed worker with idempotent processing and stale-job recovery, so a crashed or stuck job is retried instead of blocking forever.
- **Two-tier semantic caching:** final answers and retrieved chunk sets are cached by query similarity in OpenSearch.
- **Hybrid retrieval:** dense vector search combined with BM25 using reciprocal rank fusion, with the source filename boosted in the keyword query.
- **Vector store migration:** moved from a local Chroma store to OpenSearch.

## Problems I debugged

- **Retrieval missed a document it had indexed.** A query for "CS 32 Final" returned only unrelated documents. Inspecting the raw chunks showed the course name existed only in the filename metadata. I prepended a `[Source: ...]` line to each chunk before embedding and added BM25 to the retrieval path, then re-ingested the existing documents.
- **Worker looped forever on healthy jobs.** A variable was only assigned inside one branch of the stuck-job check, so any job in another state crashed the worker on every attempt. Fixed the initialization.
- **Out-of-memory crashes on a 1GB instance.** Tuned the Gunicorn workers, moved Redis off the instance to ElastiCache, and added swap.
- **413 errors on larger PDF uploads.** Nginx's default 1MB body limit; raised it for the upload route.

## Run locally

Requires Docker (MySQL) and an OpenAI API key.

```bash
# backend
cd backend
cp .env.example .env        # fill in keys and service URLs
pip install -r requirements.txt
python run.py               # http://localhost:5050

# frontend
cd frontend
npm install
npm run dev
```


## Known limitations

- Single EC2 instance with 1GB RAM; no autoscaling yet. Next step is moving compute to ECS/Fargate behind a load balancer.
- Scanned (image-only) PDFs aren't supported; they need an OCR fallback.
- The upload page sometimes needs a manual refresh to show a job's final status.
- No automated test suite yet; retrieval quality is checked with a golden-dataset builder.