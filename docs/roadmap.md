# Roadmap

This roadmap is intentionally realistic and focused on reliability.

The proposed V2 direction is defined in the
[V2 product specification](v2-product-spec.md): scoped retrieval, explicit
provenance, and a guarded update lifecycle. Its phased roadmap is
decision-gated and does not describe currently shipped behavior.

## Shipped in v1.1
- Two-stage retrieval: bi-encoder recall + cross-encoder reranking, with an adaptive result count and low-confidence bi-encoder fallback (`RERANK_*` config).
- Literal-only write discipline + `source_type` provenance (`user_statement` vs `ai_inference`).
- Persistent OAuth access tokens (survive server restarts).
- Streamable-HTTP (`/mcp`) transport fix.

## Near Term (v1.x)
- Add a lightweight CLI for common maintenance tasks (health checks, backup listing, restore dry-runs).
- Improve config ergonomics and validation messaging.
- Document stable tool response contracts with explicit version notes.

## Mid Term
- Add optional import/export utilities for chunk portability.
- Add operational docs for backup strategy and recovery drills.
- Improve conflict triage ergonomics (clearer candidate ranking and review workflow).
- Add clearer release/migration notes for persisted DB changes.

## Later
- Add optional packaging/distribution improvements (container image, pinned runtime profile).
- Explore pluggable embedding backends while keeping local-first defaults.
- Evaluate V2 implementation after its P0 contracts, migration plan, and safety criteria are approved.

## Non-Goals (Current)
- Multi-tenant SaaS deployment.
- Heavy UI-first productization.
- Overly rigid schema systems that reduce text flexibility for LLM reasoning.
