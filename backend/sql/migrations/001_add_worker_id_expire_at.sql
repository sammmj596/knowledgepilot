USE rag;

ALTER TABLE ingestion_jobs
  ADD COLUMN worker_id VARCHAR(64) NULL AFTER metadata;

ALTER TABLE ingestion_jobs
  ADD COLUMN expire_at TIMESTAMP NULL DEFAULT NULL AFTER worker_id;
