from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.vector_store import VectorStore


def main() -> None:
    store = VectorStore()
    sample_text = (
        "As of 2026-03-11, example user focus block is 6:30-9:00 AM on weekdays. "
        "This is the current default unless explicitly updated."
    )
    chunk_id = store.add_chunk(text=sample_text, source_type="example_setup")
    results = store.search(query="current deep work schedule", top_k=3)

    print(f"Stored chunk_id: {chunk_id}")
    print("Top retrieval results:")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
