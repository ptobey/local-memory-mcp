# Current Limitations

This release is usable, but intentionally early. Known constraints:

- No UI yet.
  The system is MCP-first; interaction is through assistant tool calls and scripts.

- Heuristic reconciliation can produce false positives/false negatives.
  Conflict and soft-duplicate classification is deterministic and conservative, not perfect.

- First-run model setup is manual.
  Runtime expects a local embedding model and a local cross-encoder reranker to be cached; users may need a one-time model-download step (or set `RERANK_ENABLED=false` to skip the reranker).

- Single-node/local persistence focus.
  There is no built-in multi-node replication, HA, or distributed coordination.

- No formal migration framework for data schema changes.
  Backups are provided, but schema migration/version tooling is limited.

- Auth configuration is practical, not enterprise-grade IAM.
  Modes are `none`, `bearer`, and a simple OAuth provider for MCP integrations (single static client). Issued access tokens persist across restarts, but there is no multi-user identity or per-user attribution from auth.

- API surface may evolve.
  Tool response contracts may tighten in future releases as the project matures.
