USE rag;

ALTER TABLE ingestion_jobs
  ADD COLUMN file_path VARCHAR(1024) NULL AFTER source;
