import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
INGEST_DATA_DIR = os.environ.get("INGEST_DATA_DIR", str(BASE_DIR / "data" / "files"))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "production_rag_docs")
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "200"))
RETRIEVAL_K = int(os.environ.get("RAG_RETRIEVAL_K", "4"))
RAG_MAX_RETRIES = int(os.environ.get("RAG_MAX_RETRIES", "2"))

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
MCP_ENABLED_TOOLS = os.environ.get("MCP_ENABLED_TOOLS", "tavily_search")

MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "13306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "123456")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "rag")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "")
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USER", "")
OPENSEARCH_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")