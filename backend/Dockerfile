# docker build -t rag-backend .

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .
COPY worker.py .
COPY scripts ./scripts
COPY sql ./sql

RUN mkdir -p /app/data/files

ENV FLASK_DEBUG=0 \
    INGEST_DATA_DIR=/app/data/files \
    PYTHONUNBUFFERED=1

EXPOSE 5050

CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "120", "-b", "0.0.0.0:5050", "run:app"]
