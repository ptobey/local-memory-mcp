# Setup Guide

Use one path only.

- If you want the easiest setup, use Path A (Docker).
- If you need direct Python process control (especially for local stdio clients), use Path B (Local Python).

## Path A (Recommended Easiest): Docker
Best default for most users. This path avoids local Python and virtual environment setup.

Prerequisite:
- Docker Desktop (or Docker Engine) installed and running.

1. Clone:
```bash
git clone <your-repo-url>
cd local-memory-mcp
```

2. Start:
```bash
docker compose up --build -d
```

3. Verify endpoints:
- `http://localhost:8000/mcp`
- `http://localhost:8000/sse`
- `http://localhost:8000/messages/`
- `http://localhost:8000/health`

4. Stop:
```bash
docker compose down
```

For advanced Docker usage (volume mounts, config mounts, stdio-in-container), see [`docs/docker.md`](docker.md).

## Path B: Local Python Install (Manual Prerequisites)
Use this when you want direct local Python control and desktop stdio workflows.

Requirements:
- Python 3.11+
- `pip`
- Windows, macOS, or Linux shell

1. Clone and create a virtual environment:
```bash
git clone <your-repo-url>
cd local-memory-mcp
python -m venv .venv
```

Windows PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

2. Prepare local embedding model (one-time):
```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

3. Optional local runtime config:
`config.example.json` is the commit-safe template.

Windows PowerShell:
```powershell
Copy-Item config.example.json config.json
```

macOS/Linux:
```bash
cp config.example.json config.json
```

If `config.json` is missing, built-in defaults are used:
- `MCP_AUTH_MODE=none`
- local DB path `./chroma_db`

4. Verify local write/read:
```bash
python examples/try_local.py
```

Expected:
- a chunk ID is printed
- retrieval results are printed
- `./chroma_db` is created automatically on first write

5. Run MCP over stdio (recommended local runtime):
```bash
python run_mcp_v1_stdio.py
```

6. Optional: run MCP over SSE:
```bash
python run_mcp_v1_http_sse.py
```

SSE endpoints:
- `http://localhost:8000/mcp`
- `http://localhost:8000/sse`
- `http://localhost:8000/messages/`
- `http://localhost:8000/health`

7. Optional relay for remote assistant integration:
- Keep relay tooling in a separate folder outside this repository.
- Point relay to `http://localhost:8000/sse` and `http://localhost:8000/messages/`.

For ChatGPT custom app and Claude Desktop details, see [`docs/integrations.md`](integrations.md).

## Local Data And Git Hygiene
- Local memory DB and backups are ignored by git.
- `config.json` is ignored by git and intended for local machine settings only.
- Commit-safe templates: `config.example.json` and `.env.example`.

## Troubleshooting
- `chromadb` format/version errors: use the project venv with `chromadb==1.4.1`.
- Embedding model missing: run the one-time model cache command above.
- OAuth startup errors: verify client ID/secret when `MCP_AUTH_MODE=oauth`.
