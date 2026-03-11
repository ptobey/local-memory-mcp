# Architecture

This document describes the current v5 architecture as implemented today.

## Core Components
- `src/mcp_server_v5.py`: MCP tool surface and warning/self-heal orchestration.
- `src/vector_store.py`: chunk persistence, metadata, retrieval, backup/restore.
- `src/reconciliation.py`: heuristic overlap/conflict detection and reconciliation log writes.
- `src/health_monitor.py`: oversized chunk and unresolved conflict reporting.
- `run_mcp_v5_stdio.py`: stdio MCP runner.
- `run_mcp_v5_sse_actions.py`: FastAPI SSE transport wrapper for MCP.

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
   `search(...)` performs embedding similarity query and applies lightweight ranking boosts.
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
`VectorStore.search(...)` combines:

- Embedding similarity (`score = 1 - distance`).
- Lexical overlap bonus (`query_overlap`).
- Recency bonus for time-sensitive queries (based on `last_modified`/`timestamp`).
- Update-style text bonus for state/update queries.

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

## Deprecation/Compatibility Notes
- Legacy warning top-level fields are kept for compatibility.
- Structured warning payload (`warnings[]`) should be treated as the primary integration contract going forward.
