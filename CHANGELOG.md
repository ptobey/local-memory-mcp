# Changelog

## 1.1.0 — 2026-07-11

### Added

- Docker deployment, streamable HTTP transport, persisted OAuth tokens, and
  two-stage adaptive retrieval.
- Version chains, backup/restore, health monitoring, and structured
  warning/self-heal responses.
- A focused regression suite and GitHub Actions CI for integrity-sensitive
  behavior.

### Changed

- The default Docker Compose port is loopback-only (`127.0.0.1:8000`).
- The HTTP server now refuses a non-loopback bind while authentication is
  disabled; configure bearer or OAuth before exposing it.
- Docker images and Compose services report health through `/health`.
