CREATE DATABASE IF NOT EXISTS rag
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE rag;

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id CHAR(36) NOT NULL,
    status ENUM('uploading', 'uploaded', 'chunking', 'chunked') NOT NULL DEFAULT 'uploading',
    source VARCHAR(512) NULL,
    file_path VARCHAR(1024) NULL,
    content MEDIUMTEXT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    metadata JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_ingestion_jobs_status (status),
    KEY idx_ingestion_jobs_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
