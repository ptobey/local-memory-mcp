# local-memory-mcp

**AI assistants forget everything when the conversation ends. This fixes that - locally.**

No cloud. No subscription. No account. Your data stays on your machine.

`local-memory-mcp` gives Claude, ChatGPT, and other MCP-compatible assistants a persistent memory layer powered by local vector search (ChromaDB). Tell it something once. It remembers across sessions.

<!-- demo GIF goes here -->

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Stars](https://img.shields.io/github/stars/ptobey/local-memory-mcp)

---

## The problem it solves

Every new Claude or ChatGPT session starts blank. Your preferences, your project context, your decisions - gone. You re-explain yourself constantly.

local-memory-mcp is a local MCP server that lets your AI assistant:

- Store things worth remembering ("my deep work block is 6:30–9 AM")
- Retrieve relevant context at the start of any new session
- Version and supersede memories as your situation changes
- Never send your data anywhere

It's the memory layer AI assistants should have built in, but don't.

---

## Quickstart (Docker - 2 minutes)

```bash
git clone https://github.com/ptobey/local-memory-mcp.git
cd local-memory-mcp
docker compose up --build -d
```

Then point your MCP client at `http://localhost:8000/mcp`. Done.

→ [Claude Desktop setup](docs/integrations.md) · [ChatGPT setup](docs/integrations.md) · [Manual Python install](docs/setup.md)

---

## How it works

```
[Assistant via MCP Client]
            |
            v
[run_mcp_v1_stdio.py | run_mcp_v1_http_sse.py]
            |
            v
      [src/mcp_server_v1.py]
        /          |          \
       v           v           v
[vector_store.py] [reconciliation.py] [health_monitor.py]
       |                   |
       v                   v
 [Local ChromaDB]   [Reconciliation Log]
```

**Write path:** `store`/`update` writes a chunk → reconciliation checks for overlap/conflict → returns warnings and self-heal hints when a write looks risky.

**Read path:** `search` runs semantic retrieval → ranking blends similarity with lightweight lexical/recency signals → deprecated chunks stay hidden unless explicitly requested.

---

## Features

- MCP tools: `store`, `search`, `update`, `delete`, `get_chunk`, `get_evolution_chain`
- Versioned updates (`strategy="version"`) with supersedes chains
- Soft delete by default (history retained), optional hard delete
- Heuristic reconciliation and conflict logging
- Warning-first write responses with structured `warnings[]` and self-heal fields
- Health checks for oversized chunks and unresolved conflicts
- Local backup/restore for the persisted vector DB
- Stdio and SSE transports
- Optional SSE auth: `none` (local-only), `bearer`, or `oauth`

---

## The design idea behind it (AIX)

AIX (AI eXperience) means designing for how LLMs actually consume context, not how humans file documents:

- Prefer clear text chunks over rigid document schemas
- Keep metadata minimal but useful: timestamps, confidence, supersedes links, deprecation flags
- Preserve history with version chains instead of destructive overwrites
- Return warning-rich tool responses so the model can self-correct

The goal is practical retrieval quality and reliable AI behavior, not perfect human taxonomies.

---

## Example workflow

**Store a memory:**

```json
tool: store
input: { "text": "Weekday focus block is 6:30-9:00 AM, current default schedule." }
```

**Retrieve it later:**

```json
tool: search
input: { "query": "current deep work schedule", "top_k": 5 }
```

**Bootstrap a new session** by running a few focused retrievals, then synthesizing only active, non-deprecated chunks into a short brief for the new model instance. More flows in [`examples/`](examples).

---

## Privacy & deployment

- Local-first and user-controlled by default
- Data stored in local ChromaDB files under the configured persist directory
- No cloud backend required; optional remote access via user-managed tunneling
- Never commit real secrets - use local config/env values

---

## Documentation

- [Setup guide](docs/setup.md)
- [Integrations (Claude Desktop + ChatGPT)](docs/integrations.md)
- [Architecture](docs/architecture.md)
- [AIX notes](docs/aix.md)
- [Docker guide](docs/docker.md)
- [Limitations](docs/limitations.md)
- [Roadmap](docs/roadmap.md)

---

## License

MIT. See [`LICENSE`](LICENSE).
