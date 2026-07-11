# V2 Product Specification: Scoped, Provenance-Safe Memory

<!-- markdownlint-disable MD013 -->

Status: proposed  
Audience: maintainers, MCP client authors, and reviewers  
Target outcome: any authorized AI connected to the MCP can retrieve memory relevant to the user's current topics and safely save or manage changes.

## 1. Product decision

V2 evolves Local Memory from one global semantic collection into a scoped memory service with explicit provenance and a guarded update lifecycle. Text chunks remain the primary unit. The MCP, not a particular model or client, owns the durable rules for scope, lineage, and write safety.

An assistant should be able to say, in effect: “I am working in these workspaces on these topics; give me the active, relevant context and enough source information to use it responsibly.” It should then be able to propose or apply a change without silently overwriting history, crossing workspace boundaries, or converting an inference into a user statement.

The default remains local-first, single-user, and useful with minimal setup.

### Success criteria

- A client can establish current workspace/topic context once, then retrieve active relevant memory without inventing search filters on every call.
- Every returned memory explains where it came from, whether it is current, and which scope made it eligible.
- Writes are scoped, attributable, idempotent, and versioned by default.
- Ambiguous, conflicting, destructive, or cross-scope changes are proposed for confirmation instead of being silently applied.
- Existing V1 data and tool calls remain usable through a documented compatibility period.

## 2. User and agent mental model

The user has **memory**, divided into **workspaces** and described by **topics**:

- A workspace is a durable boundary such as `personal`, `work/acme`, or `project/local-memory-mcp`. It controls where a memory belongs and prevents unrelated contexts from leaking into retrieval or updates.
- A topic is a lightweight, many-to-many retrieval hint such as `roadmap`, `travel`, or `communication-style`. Topics are not folders and do not grant access.
- The current context is a session-scoped set of eligible workspaces plus topic/query signals. It narrows retrieval; it does not move or copy records.
- A memory is an immutable revision in an evolution chain. One revision is active; prior revisions remain history unless explicitly purged.
- A source describes the evidence behind a memory. “The user said this” and “an assistant inferred this” are visibly different claims.

The agent follows a simple loop:

1. Set or pass the current context.
2. Retrieve, citing returned provenance and preferring active revisions.
3. Save net-new facts or propose changes to existing facts.
4. Resolve warnings or request user confirmation when authority is insufficient.
5. Verify the resulting active state.

Users should not need to understand embeddings, chunks, or reconciliation to correct memory. Clients may present “remember,” “update,” “forget,” “show history,” and “why do you know this?” using the same underlying tools.

## 3. Scope model

### 3.1 Workspace rules

Every V2 record has exactly one `workspace_id`. Workspace IDs are stable, opaque identifiers; a mutable display name and optional parent are separate properties. Hierarchy is organizational only: selecting a parent does not include descendants unless `include_descendants=true` is explicit.

The server maintains an allowlist of workspaces available to the authenticated connection. A call cannot widen that grant. In local/no-auth mode, the installation is still one security principal, but scope checks behave identically so moving to authenticated access does not change data semantics.

Defaults are conservative:

- New installations create a `personal` workspace.
- A session with no context searches only its configured default workspace.
- Cross-workspace search requires explicit workspace IDs or an established session context containing them.
- Writes require one explicit or session-default workspace; there is no “all workspaces” write.
- Moving a record between workspaces is a distinct, confirmable operation with an audit event.

### 3.2 Topic rules

`topic_ids` are optional normalized labels attached by the user, client, or server suggestion. Retrieval combines semantic similarity with topic eligibility; a topic match may boost or filter candidates but must never override workspace access.

Clients can supply free-text `current_topics` when stable labels are not known. The server may return `suggested_topics`, but suggestions are not persisted as user-authored metadata without a write. V2 avoids a mandatory taxonomy: topic aliases and merges can be added later without rewriting memory text.

### 3.3 Retrieval contract

Eligibility is evaluated before semantic ranking:

1. authorized workspace intersection;
2. active revisions by default (`include_history=false`);
3. optional topic and source filters;
4. semantic candidate retrieval and reranking;
5. diversity and token-budget selection so one dense subtopic does not crowd out the rest.

Each result includes `scope_match` (workspace and matched topics), ranking scores, lifecycle status, and provenance. Results from different workspaces remain labeled and are never blended into a synthetic stored fact.

## 4. Source and provenance

V2 replaces the overloaded V1 `source_type` flag with a structured, append-only provenance envelope while preserving the original value during migration.

Required fields:

| Field | Meaning |
| --- | --- |
| `claim_origin` | `user_statement`, `agent_inference`, `imported`, or `system_observation` |
| `recorded_at` | Server timestamp for this revision |
| `recorded_by` | Stable connection/client identity when available; otherwise `local-anonymous` |
| `evidence_refs` | Zero or more references to conversation turns, files, URLs, or memory IDs |
| `confidence` | Optional confidence in the claim, not source authority |
| `user_confirmed` | Whether the user explicitly confirmed this revision |

Evidence references store a type, stable locator or content hash, and optional captured title/time. They do not copy external content by default. A source locator is metadata, not proof that the source remains accessible. Secrets, bearer tokens, raw prompts, and hidden chain-of-thought must never be recorded as provenance.

Authority rules:

- Only literal user-provided content may use `user_statement`.
- Agent synthesis uses `agent_inference` and cannot silently supersede a conflicting user statement.
- Imported claims retain import origin and source reference even if an agent reformats them.
- A later explicit user confirmation creates a new revision or confirmation event; it does not rewrite the original origin.
- Retrieval exposes provenance consistently so clients can qualify uncertain or inferred claims.

## 5. Update lifecycle and safety

### 5.1 States and operations

A logical memory has immutable revisions with these states: `proposed`, `active`, `superseded`, `deprecated`, or `tombstoned`. The normal lifecycle is:

```text
proposed --confirm/apply--> active --version--> superseded
                              |                  |
                              +--deprecate-------+
hard purge (separate privileged operation) --> tombstoned audit marker
```

`save_memory` creates a net-new active revision when risk is low. `propose_update` compares a candidate with the expected active revision and returns a preview. `apply_update` activates that proposal only if its preconditions still hold. Soft deprecation is reversible; hard purge requires explicit confirmation and is excluded from ordinary agent autonomy.

### 5.2 Guardrails

All mutations require:

- `workspace_id` (explicit or unambiguous session default);
- `idempotency_key`, scoped to client and operation;
- provenance;
- `expected_revision_id` for updates, deprecations, and moves;
- a declared `reason` for lifecycle changes.

The server returns a machine-readable decision: `applied`, `confirmation_required`, `conflict`, `rejected`, or `no_op`. A retry with the same idempotency key returns the original result. Optimistic concurrency rejects stale updates rather than branching silently.

Confirmation is required for cross-workspace moves, hard purge, bulk changes, reducing a user-stated claim to an inference, unresolved contradictions, and updates without a matching expected revision. Server policy may require confirmation for additional categories. Confirmation tokens are short-lived, bind the exact preview/hash and principal, and cannot authorize a modified payload.

After an applied mutation, the response includes the new revision, superseded/deprecated IDs, warnings, and `verification_queries`. Reconciliation may suggest cleanup but cannot autonomously delete unrelated active memories.

## 6. Proposed MCP surface

Names are provisional, but the separation of context, retrieval, preview, and commit is normative.

### Establish current context

```json
{
  "tool": "set_context",
  "input": {
    "workspace_ids": ["ws_project_local_memory"],
    "current_topics": ["V2 roadmap", "safe updates"],
    "ttl_seconds": 3600
  }
}
```

The response returns a `context_id`, effective workspace grant, normalized topics, and expiry. Stateless clients may pass the same fields directly to retrieval calls.

### Retrieve context for current topics

```json
{
  "tool": "recall",
  "input": {
    "context_id": "ctx_01J...",
    "query": "What decisions and open constraints matter for the V2 spec?",
    "max_tokens": 1800,
    "include_history": false
  }
}
```

Each result returns `memory_id`, `revision_id`, `text`, `workspace_id`, `topic_ids`, `status`, `provenance`, `scope_match`, and ranking fields. The response also reports searched workspaces, omitted-result counts/reasons, and a retrieval receipt usable by a subsequent mutation.

### Save a net-new user statement

```json
{
  "tool": "save_memory",
  "input": {
    "workspace_id": "ws_project_local_memory",
    "text": "V2 remains local-first and single-user by default.",
    "topic_ids": ["v2", "product-principles"],
    "provenance": {"claim_origin": "user_statement", "evidence_refs": ["turn:184"]},
    "idempotency_key": "turn-184-principle-1"
  }
}
```

If likely matches exist, the server returns `confirmation_required` with candidates and recommends an update instead of creating a parallel active fact.

### Preview and apply an update

```json
{
  "tool": "propose_update",
  "input": {
    "memory_id": "mem_01H...",
    "expected_revision_id": "rev_07",
    "new_text": "V2 beta supports personal and project workspaces.",
    "reason": "User narrowed the initial workspace rollout.",
    "provenance": {"claim_origin": "user_statement", "evidence_refs": ["turn:191"]},
    "retrieval_receipt": "receipt_01J...",
    "idempotency_key": "turn-191-update-1"
  }
}
```

```json
{
  "tool": "apply_update",
  "input": {
    "proposal_id": "proposal_01J...",
    "confirmation_token": "confirm_01J...",
    "idempotency_key": "turn-191-apply-1"
  }
}
```

Low-risk policies may apply a proposal immediately and return `applied`; clients must handle both paths. Inspection tools include `get_memory`, `get_history`, `list_proposals`, and `explain_retrieval`. Maintenance tools include scoped deprecation, restore, backup, and health checks.

## 7. Backwards compatibility and migration

V2 launches alongside V1 rather than changing V1 tool meanings in place.

1. **Inventory and backup.** A read-only preflight reports counts, invalid metadata, dangling chains, and estimated migration actions; migration requires a verified backup.
2. **Additive transform.** Each V1 chunk receives a stable V2 `memory_id`/`revision_id`, default workspace, and empty topic list. Existing chunk IDs are retained as aliases. `timestamp`, `last_modified`, `supersedes`, `deprecated`, and confidence are preserved.
3. **Provenance mapping.** `source_type=user_statement` maps to `claim_origin=user_statement`; `ai_inference` maps to `agent_inference`; unknown values map to `imported` with the raw value retained in `legacy_metadata`. Migrated claims set `user_confirmed=false` unless confirmation is independently known.
4. **Chain validation.** Valid supersedes chains become revision histories. Ambiguous forks or missing parents remain separate memories and produce review issues; migration never guesses a destructive merge.
5. **Compatibility window.** V1 tools continue against the default workspace through an adapter. V1 reads expose active V2 revisions in their old shape. V1 writes are tagged `api_version=v1`, use server-generated idempotency keys, and cannot access non-default workspaces.
6. **Cutover and rollback.** Operators compare counts and deterministic sample queries before enabling V2 writes. Rollback restores the preflight backup; no downgrade attempts to flatten V2-only multi-workspace changes into V1.

Migration is restartable and records a checkpoint plus per-record outcome. The compatibility adapter is deprecated only after at least one minor release with usage telemetry available locally (never transmitted by default) and a documented export path.

## 8. Non-goals

- Multi-user collaboration, enterprise tenancy, or cloud control planes.
- Automatic ingestion of every conversation, file, or browsing event.
- A universal ontology, knowledge graph, or mandatory folder hierarchy.
- Treating model-generated summaries as equivalent to user testimony.
- Silent autonomous conflict resolution, bulk deletion, or hard purge.
- Fine-grained document ACLs beyond workspace grants in the initial V2 release.
- Replacing source systems or guaranteeing that external evidence remains available.
- A full graphical memory-management application.

## 9. Prioritized roadmap

### P0 — Contract and safe foundation

- Finalize identifiers, record/provenance schema, workspace authorization semantics, error envelope, and capability discovery.
- Add additive storage/schema versioning, migration preflight, backup gate, resumable migration, and rollback documentation.
- Implement workspace-filtered retrieval before ranking; default workspace adapter for V1.
- Add immutable revisions, optimistic concurrency, idempotency, proposal/apply flow, and audit events.
- Ship contract tests proving no cross-workspace reads/writes and no inference-to-user provenance escalation.

Exit: migrated V1 stores behave equivalently in the default workspace, and destructive/cross-scope mutations fail closed.

### P1 — Topic-aware beta

- Add session context, topic labels/free-text topics, token-budgeted recall, retrieval receipts, and provenance-rich results.
- Add duplicate/update candidate detection scoped to a workspace and explainable confirmation prompts.
- Provide `get_memory`, history, proposal review, scoped health checks, and client integration examples.

Exit: two different MCP clients can retrieve and safely maintain the same scoped memory with consistent results.

### P2 — Operational maturity

- Add topic alias/merge management, workspace move workflow, scoped import/export, and restore drills.
- Add policy configuration for confirmation thresholds and local-only compatibility usage reporting.
- Benchmark relevance, stale-fact rate, provenance completeness, and false confirmation prompts on representative stores.

Exit: maintainers can measure quality, recover safely, and support the compatibility sunset decision.

### P3 — Optional expansion

- Explore finer-grained grants, additional provenance adapters, and a lightweight review UI.
- Evaluate pluggable embeddings/rerankers without changing scope or lifecycle guarantees.

These are optional only after P0–P2 safety and migration criteria are met.

## 10. Open decisions for implementation review

1. Whether context state lives only in the MCP session or also supports signed, portable context tokens.
2. Whether low-risk `propose_update` may auto-apply by default or only under an explicit server policy.
3. The minimum evidence reference retained for conversation-derived user statements when a client cannot provide stable turn IDs.
4. How long the V1 compatibility window lasts; the spec requires at least one minor release, but maintainers should set a date only after beta evidence.
