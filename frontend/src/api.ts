export type ChatRole = "user" | "assistant";

export type ChatMessage = { role: ChatRole; content: string };

export type UploadJobResult = {
  job_id: string;
  status: string;
  source?: string;
  file_path?: string;
  filename?: string;
};

export type VectorizeJobResult = {
  job_id: string;
  status: string;
  chunks_indexed?: number;
  file_path?: string;
  source?: string;
};

export type IngestionJob = {
  id: string;
  status: string;
  source?: string;
  file_path?: string;
  chunk_count: number;
  error_message?: string | null;
  created_at?: string | null;
};

const CHAT_URL = "/api/chat";
const INGEST_FILE_URL = "/api/ingest/file";

async function parseJsonResponse<T>(res: Response): Promise<T> {
  let data: T & { error?: string };
  try {
    data = (await res.json()) as T & { error?: string };
  } catch {
    throw new Error(
      res.status === 404
        ? "API not found: make sure the backend is running on port 5050 and use npm run dev (not a static preview without the proxy)."
        : `Server returned a non-JSON response (${res.status})`
    );
  }
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

export async function sendChat(
  messages: ChatMessage[]
): Promise<ChatMessage> {
  const res = await fetch(CHAT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  const data = await parseJsonResponse<{ message?: ChatMessage }>(res);
  if (!data.message?.content) {
    throw new Error("Invalid response from server");
  }
  return data.message;
}

export async function uploadDocument(
  file: File,
  options?: { source?: string }
): Promise<UploadJobResult> {
  const form = new FormData();
  form.append("file", file);
  if (options?.source?.trim()) {
    form.append("source", options.source.trim());
  }
  const res = await fetch(INGEST_FILE_URL, {
    method: "POST",
    body: form,
  });
  return parseJsonResponse<UploadJobResult>(res);
}

export async function vectorizeJob(jobId: string): Promise<VectorizeJobResult> {
  const res = await fetch(`/api/ingest/jobs/${encodeURIComponent(jobId)}/vectorize`, {
    method: "POST",
  });
  return parseJsonResponse<VectorizeJobResult>(res);
}

export async function listIngestionJobs(): Promise<IngestionJob[]> {
  const res = await fetch("/api/ingest/jobs");
  const data = await parseJsonResponse<{ jobs: IngestionJob[] }>(res);
  return data.jobs;
}
