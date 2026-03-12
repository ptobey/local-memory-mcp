FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Cache the embedding model inside the image so runtime can stay offline-first.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY src /app/src
COPY run_mcp_v1_http_sse.py /app/run_mcp_v1_http_sse.py
COPY run_mcp_v1_stdio.py /app/run_mcp_v1_stdio.py
COPY config.example.json /app/config.example.json

RUN useradd --create-home --uid 10001 appuser && \
    mkdir -p /app/chroma_db /app/backups && \
    chown -R appuser:appuser /app

USER appuser

ENV MCP_BIND_HOST=0.0.0.0 \
    MCP_BIND_PORT=8000

EXPOSE 8000

CMD ["python", "run_mcp_v1_http_sse.py"]
