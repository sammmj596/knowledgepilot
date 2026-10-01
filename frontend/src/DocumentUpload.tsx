import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { IngestionJob, UploadJobResult, VectorizeJobResult } from "./api";
import { listIngestionJobs, uploadDocument, vectorizeJob } from "./api";

const PENDING_TIMEOUT_MS = 5 * 60 * 1000;
const ACCEPT = ".txt,.md,.markdown,.csv,.json,.html,.htm,.pdf,.docx,.log,.rst";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function DocumentUpload() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [vectorizingId, setVectorizingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadJobResult | null>(null);
  const [vectorizeResult, setVectorizeResult] = useState<VectorizeJobResult | null>(null);
  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  // Jobs submitted for indexing, keyed by id -> time submitted. The database keeps
  // them as "uploaded" until the worker picks them up, so we track them here.
  const [pending, setPending] = useState<Record<string, number>>({});

  const refreshJobs = useCallback(async () => {
    try {
      const rows = await listIngestionJobs();
      setJobs(rows);
    } catch {
      // keep silent; upload errors surface separately
    }
  }, []);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const ids = Object.keys(pending);
    const done = ids.filter((id) => {
      const job = jobs.find((j) => j.id === id);
      return job?.status === "chunked" || Date.now() - pending[id] > PENDING_TIMEOUT_MS;
    });
    if (done.length > 0) {
      setPending((prev) => {
        const next = { ...prev };
        done.forEach((id) => delete next[id]);
        return next;
      });
      return;
    }
    const hasActiveJob = jobs.some(
      (job) => job.status === "queued" || job.status === "chunking"
    );
    if (ids.length === 0 && !hasActiveJob) return;

    const interval = setInterval(() => {
      void refreshJobs();
    }, 2000);
    return () => clearInterval(interval);
  }, [pending, jobs, refreshJobs]);

  function pickFile(next: File | null) {
    setFile(next);
    setError(null);
    setUploadResult(null);
    setVectorizeResult(null);
    if (next && !source.trim()) {
      setSource(next.name);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    pickFile(e.target.files?.[0] ?? null);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0] ?? null);
  }

  async function handleUpload(e: FormEvent) {
    e.preventDefault();
    if (!file || uploading) return;
    setError(null);
    setUploadResult(null);
    setVectorizeResult(null);
    setUploading(true);
    try {
      const data = await uploadDocument(file, {
        source: source.trim() || undefined,
      });
      setUploadResult(data);
      setFile(null);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
      await refreshJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleVectorize(jobId: string) {
    setError(null);
    setVectorizeResult(null);
    setVectorizingId(jobId);
    try {
      const data = await vectorizeJob(jobId);
      setVectorizeResult(data);
      setPending((prev) => ({ ...prev, [jobId]: Date.now() }));
      setUploadResult((prev) =>
        prev?.job_id === jobId ? { ...prev, status: data.status } : prev
      );
      await refreshJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Indexing failed");
    } finally {
      setVectorizingId(null);
    }
  }

  return (
    <section className="upload-page" aria-label="Documents">
      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Documents</h2>
          <p className="panel-desc">
            Step 1: upload a file. Step 2: click “Index document” to extract text, chunk it, and add it to the search index.
          </p>
        </div>

        <form className="upload-form" onSubmit={(e) => void handleUpload(e)}>
          <div
            className={`dropzone${dragOver ? " dropzone-active" : ""}${file ? " dropzone-has-file" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="file-input"
              onChange={handleFileChange}
            />
            {file ? (
              <div className="file-summary">
                <span className="file-icon" aria-hidden>
                  📄
                </span>
                <div>
                  <div className="file-name">{file.name}</div>
                  <div className="file-meta">{formatBytes(file.size)}</div>
                </div>
              </div>
            ) : (
              <div className="dropzone-placeholder">
                <span className="file-icon" aria-hidden>
                  ⬆
                </span>
                <p>Click to choose a file, or drag it here</p>
                <p className="dropzone-hint">PDF · DOCX · TXT · MD · HTML …</p>
              </div>
            )}
          </div>

          <label className="field">
            <span className="field-label">Source label (optional)</span>
            <input
              className="field-input"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="Defaults to the file name"
              disabled={uploading}
            />
          </label>

          {error && <div className="banner-error">{error}</div>}

          {uploadResult && (
            <div className="result-card" role="status">
              <div className="result-title">File uploaded</div>
              <dl className="result-list">
                <div>
                  <dt>Job ID</dt>
                  <dd>{uploadResult.job_id}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{uploadResult.status}</dd>
                </div>
                <div>
                  <dt>Stored at</dt>
                  <dd>{uploadResult.file_path || "—"}</dd>
                </div>
              </dl>
              <button
                type="button"
                className="send send-inline"
                disabled={
                  vectorizingId === uploadResult.job_id ||
                  uploadResult.status !== "uploaded"
                }
                onClick={() => void handleVectorize(uploadResult.job_id)}
              >
                {vectorizingId === uploadResult.job_id ? "Indexing…" : "Index document"}
              </button>
            </div>
          )}

          {vectorizeResult && (
            <div className="result-card" role="status">
              <div className="result-title">Indexing complete</div>
              <dl className="result-list">
                <div>
                  <dt>Job ID</dt>
                  <dd>{vectorizeResult.job_id}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{vectorizeResult.status}</dd>
                </div>
                <div>
                  <dt>Chunks</dt>
                  <dd>{vectorizeResult.chunks_indexed ?? "—"}</dd>
                </div>
              </dl>
            </div>
          )}

          <div className="upload-actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={uploading || !file}
              onClick={() => {
                pickFile(null);
                if (inputRef.current) inputRef.current.value = "";
              }}
            >
              Clear
            </button>
            <button type="submit" className="send" disabled={uploading || !file}>
              {uploading ? "Uploading…" : "Upload file"}
            </button>
          </div>
        </form>

        {jobs.length > 0 && (
          <div className="job-list">
            <h3 className="job-list-title">Recent jobs</h3>
            <ul className="job-items">
              {jobs.map((job) => (
                <li key={job.id} className="job-item">
                  <div className="job-item-main">
                    <span className="job-source">{job.source || job.file_path || job.id}</span>
                    <span className={`job-status job-status-${pending[job.id] && job.status === "uploaded" ? "queued" : job.status}`}>
                      {pending[job.id] && job.status === "uploaded" ? "queued" : job.status}
                    </span>
                  </div>
                  <div className="job-item-meta">
                    {job.file_path && <span>{job.file_path}</span>}
                    {job.status === "chunked" && <span>{job.chunk_count} chunks</span>}
                    {job.error_message && <span className="job-error">{job.error_message}</span>}
                  </div>
                  {job.status === "uploaded" && job.file_path && !pending[job.id] && (
                    <button
                      type="button"
                      className="btn-secondary btn-small"
                      disabled={vectorizingId === job.id}
                      onClick={() => void handleVectorize(job.id)}
                    >
                      {vectorizingId === job.id ? "Indexing…" : "Index document"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
