# AIX (AI eXperience)

AIX is the design principle used in this project: optimize memory systems for how LLMs actually use context.

## Core Idea
Most assistant memory systems overfit to human organization (folders, rigid schemas, dense forms). LLMs usually perform better when given:

- clean text chunks,
- enough metadata for temporal/state reasoning,
- and explicit update history.

Second Brain v5 is intentionally text-first with minimal structure.

## Why Text-First + Lightweight Metadata
- Text-first keeps high semantic density for retrieval and synthesis.
- Minimal metadata avoids schema drift and migration burden.
- Supersedes/deprecation links preserve evolving state without erasing history.
- Warning-rich write responses help the model avoid stale parallel summaries.

## Design Lessons From v5
- Chunk quality matters more than rigid document decomposition.
- Versioning state changes is safer than overwriting.
- Retrieval benefits from small pragmatic bonuses (lexical overlap + recency) layered on top of embeddings.
- Reconciliation should be conservative; uncertain cases are logged for explicit resolution.
- Local-first defaults keep privacy and operational control straightforward.

## What AIX Is Not
- It is not a claim that metadata is unimportant.
- It is not anti-structure; it uses just enough structure to support reliable AI behavior.
- It is not a generic enterprise knowledge graph platform.

For architecture details, see `docs/architecture.md`.
