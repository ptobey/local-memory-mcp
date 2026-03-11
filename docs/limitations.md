# Current Limitations

This release is usable, but intentionally early. Known constraints:

- No UI yet.
  The system is MCP-first; interaction is through assistant tool calls and scripts.

- Heuristic reconciliation can produce false positives/false negatives.
  Conflict and soft-duplicate classification is deterministic and conservative, not perfect.

- First-run model setup is manual.
  Runtime expects local embedding model availability; users may need a one-time cache step.

- Single-node/local persistence focus.
  There is no built-in multi-node replication, HA, or distributed coordination.

- No formal migration framework for data schema changes.
  Backups are provided, but schema migration/version tooling is limited.

- Auth configuration is practical, not enterprise-grade IAM.
  Modes are `none`, `bearer`, and simple in-memory OAuth provider support for MCP integrations.

- API surface may evolve.
  Warning payloads keep backward compatibility today, but integration contracts may tighten in future releases.
