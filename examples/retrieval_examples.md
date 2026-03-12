# Retrieval Examples

These examples show MCP tool usage patterns, not a strict client SDK.

## Store memory chunk
```text
tool: store
input:
{
  "text": "As of 2026-03-08, example user focus block is 6:30-9:00 AM on weekdays."
}
```

## Retrieve current schedule
```text
tool: search
input:
{
  "query": "current deep work schedule",
  "top_k": 5
}
```

## Update an existing state chunk (version strategy)
```text
tool: update
input:
{
  "chunk_id": "<existing_chunk_id>",
  "new_text": "Update: example user focus block is now 7:00-9:30 AM on weekdays.",
  "strategy": "version"
}
```

## Soft-delete stale parallel chunk
```text
tool: delete
input:
{
  "chunk_id": "<stale_chunk_id>",
  "hard_delete": false
}
```

## Bootstrap a new LLM session
Recommended retrieval queries:

```text
search("current priorities and deadlines")
search("active preferences and communication style")
search("current schedule constraints")
search("recent corrections or superseded assumptions")
```

Then build a session brief using active non-deprecated chunks first, and include older chunks only as historical context.
