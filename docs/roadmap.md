# Roadmap

This roadmap is intentionally realistic and focused on reliability.

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

## Non-Goals (Current)
- Multi-tenant SaaS deployment.
- Heavy UI-first productization.
- Overly rigid schema systems that reduce text flexibility for LLM reasoning.
