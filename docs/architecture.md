# Architecture

This document describes the current v1 architecture as implemented today.

## Core Components
- `src/mcp_server_v1.py`: MCP tool surface and warning/self-heal orchestration.
- `src/vector_store.py`: chunk persistence, metadata, retrieval, backup/restore.
- `src/reconciliation.py`: heuristic overlap/conflict detection and reconciliation log writes.
- `src/health_monitor.py`: oversized chunk and unresolved conflict reporting.
- `src/oauth_provider.py`: simple OAuth provider for the SSE/HTTP transports; issued access tokens persist to disk so restarts don't drop sessions.
- `run_mcp_v1_stdio.py`: stdio MCP runner.
- `run_mcp_v1_http_sse.py`: FastAPI HTTP/SSE transport wrapper for MCP.

## Storage Model
- Primary memory collection: `master_memory`.
- Reconciliation log collection: `reconciliation_log`.
- Backing store: local Chroma persistent directory (`CHROMADB_PERSIST_DIR`).

## Chunk Lifecycle
1. Ingest
   `store(text)` calls `VectorStore.add_chunk(...)`.
2. Metadata assignment
   Base metadata is attached at write time (timestamps, confidence, source type, deprecation flags, supersedes pointer, word count).
3. Reconciliation pass
   New writes are checked against active chunks to detect overlap/contradiction/soft duplicates.
4. Retrieval
   `search(...)` runs two-stage bi-encoder recall + cross-encoder rerank with an adaptive result count (see Retrieval Model).
5. Evolution
   `update(strategy="version")` creates a new chunk, links it with `supersedes`, and deprecates the old chunk.
6. Deprecation or deletion
   `delete(hard_delete=False)` marks deprecated by default; hard delete is optional.
7. Maintenance
   `get_issues`, `self_check`, `create_backup`, and `restore_backup` support health and recovery workflows.

## Metadata Usage
Current chunk metadata fields used by retrieval and maintenance:

- `timestamp`: creation time (ISO-8601 UTC).
- `last_modified`: latest modification time.
- `confidence`: chunk confidence score.
- `deprecated`: active/deprecated switch.
- `supersedes`: parent chunk ID for version chains.
- `source_type`: source classification (for example `user_statement`).
- `word_count`: chunk size.
- `access_count`: retrieval count.
- `last_accessed`: most recent retrieval timestamp.

Metadata is intentionally lightweight. Most semantics stay in chunk text so LLMs can reason directly.

## Retrieval Model
`VectorStore.search(...)` is a two-stage pipeline, all config-tunable via `RERANK_*` keys:

1. **Bi-encoder recall.** Embed the query and pull a wide candidate pool of
   `top_k * RERANK_OVERFETCH` chunks by cosine similarity (`score = 1 - distance`).
2. **Cross-encoder rerank.** A local reranker (`RERANKER_MODEL`, default
   `cross-encoder/ms-marco-MiniLM-L-6-v2`) scores every `(query, chunk)` pair and
   reorders by relevance. Loaded once and kept warm; if it can't load, search
   degrades gracefully to bi-encoder ordering.
3. **Adaptive result count.** `top_k` is a target, not a hard cap: results extend
   past it while the rerank score stays above `RERANK_SCORE_THRESHOLD` (up to a
   `RERANK_MAX_K` ceiling), so a tight cluster of relevant chunks isn't cut off.
   When the reranker's best score is below `RERANK_LOWCONF_FLOOR` — it can't
   confidently discriminate, common on broad queries — it falls back to bi-encoder
   ordering.

`recency_score` and `query_overlap` are returned as informational fields for the
assistant; they do not change the ranking. Set `RERANK_ENABLED=false` for plain
fixed-`top_k` bi-encoder retrieval.

Default behavior excludes deprecated chunks. Set `include_deprecated=True` for history-sensitive tasks.

## Versioning and Deprecation Model
- Preferred update mode: `update(..., strategy="version")`.
  - Creates a new chunk.
  - Sets `supersedes=<old_chunk_id>` on the new chunk.
  - Marks old chunk deprecated.
- Alternative mode: `strategy="replace"` updates in place.
- Soft delete (`hard_delete=False`) marks deprecated and preserves history.
- Hard delete removes the chunk from storage.
- Evolution lineage is read via `get_evolution_chain(chunk_id)`.

## Warning and Self-Heal Contract
`store` and `update` can return warning payloads that include:

- `warnings[]` with codes, severity, reasons, and actions.
- Summary/context fields (`warning_summary`, `warning_context`).
- Self-heal contract fields (`self_heal_required`, `self_heal_steps`, and related metadata).

This is designed so assistant clients can run deterministic remediation loops before finalizing user-facing responses.
