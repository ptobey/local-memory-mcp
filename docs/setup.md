# Local Setup Guide

## Requirements
- Python 3.11+
- `pip`
- OS: Windows, macOS, or Linux

## 1. Clone and create virtual environment
```bash
git clone <your-repo-url>
cd chunking
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

## 2. Prepare local embedding model (one-time)
The server enforces offline model use during runtime. Make sure the model is cached locally first:

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```

## 3. Configure runtime
`config.example.json` is the commit-safe template.  
Create `config.json` only if you want local overrides:

Windows PowerShell:
```powershell
Copy-Item config.example.json config.json
```

macOS/Linux:
```bash
cp config.example.json config.json
```

If `config.json` is missing, built-in defaults are used. Default posture is local-first:

- `MCP_AUTH_MODE`: `none`
- DB path: `./chroma_db`

Optional auth modes:

- `bearer`: set `MCP_BEARER_TOKEN` in `config.json` or environment
- `oauth`: set `MCP_OAUTH_CLIENT_ID` and `MCP_OAUTH_CLIENT_SECRET`

Environment-based auth examples are in `.env.example`.

## 4. Run a local verification pass
This checks that write and retrieval are working before wiring a client:

```bash
python examples/try_local.py
```

Expected result:
- A new chunk ID is printed.
- Retrieval results are printed for the sample query.
- `./chroma_db` is created automatically on first write.

## 5. Run MCP over stdio (recommended first)
```bash
python run_mcp_v5_stdio.py
```

## 6. Run MCP over SSE (optional)
```bash
python run_mcp_v5_sse_actions.py
```

Endpoints:

- `http://localhost:8000/mcp`
- `http://localhost:8000/sse`
- `http://localhost:8000/messages/`
- `http://localhost:8000/health`

## 7. Optional relay for remote assistant integration
If you need remote callback-based integrations:

1. Keep relay tooling in a separate folder outside this repository.
2. Point your relay to the local SSE service:
   - `http://localhost:8000/sse`
   - `http://localhost:8000/messages/`

For ChatGPT custom app and Claude Desktop integration details, see [`docs/integrations.md`](integrations.md).

## Local Data And Git Hygiene
- Local memory DB and backups are ignored by git.
- `config.json` is ignored by git and intended for local machine settings only.
- Keep only templates in commits: `config.example.json` and `.env.example`.

## Troubleshooting
- `chromadb` format/version errors: use the project venv with `chromadb==1.4.1`.
- Embedding model missing: run the one-time model cache command above.
- OAuth startup errors: verify client ID/secret are set when `MCP_AUTH_MODE` is `oauth`.
