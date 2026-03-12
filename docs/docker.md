# Docker Guide

This project can run well in Docker for HTTP/SSE MCP usage.

## What Docker Is Good For Here
- Running `run_mcp_v1_http_sse.py` as a local service.
- Keeping runtime dependencies isolated.
- Persisting local memory DB and backups via Docker volumes.

## Build
```bash
docker build -t local-memory-mcp:latest .
```

## Run (HTTP/SSE)
```bash
docker run --rm -p 8000:8000 \
  -v local_memory_chroma_db:/app/chroma_db \
  -v local_memory_backups:/app/backups \
  local-memory-mcp:latest
```

Endpoints:
- `http://localhost:8000/mcp`
- `http://localhost:8000/sse`
- `http://localhost:8000/messages/`
- `http://localhost:8000/health`

## Run With Compose
```bash
docker compose up --build -d
```

Stop:
```bash
docker compose down
```

## Using Local `config.json`
The container uses built-in defaults unless you mount a config file.

Example:
```bash
docker run --rm -p 8000:8000 \
  -v ${PWD}/config.json:/app/config.json:ro \
  -v local_memory_chroma_db:/app/chroma_db \
  -v local_memory_backups:/app/backups \
  local-memory-mcp:latest
```

## Stdio In Docker
Stdio can work in Docker, but it is less ergonomic than HTTP/SSE:

- You must run container with `-i` so stdin/stdout stay attached.
- Desktop MCP clients that spawn local processes often work better with direct Python entrypoints.

Example stdio command:
```bash
docker run --rm -i \
  -v ${PWD}/config.json:/app/config.json:ro \
  -v local_memory_chroma_db:/app/chroma_db \
  local-memory-mcp:latest \
  python run_mcp_v1_stdio.py
```

Recommendation: use Docker for HTTP/SSE integrations; use local Python for desktop stdio unless you specifically need containerized stdio.
