import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mcp_server_v1 import record_retrieval_feedback
from src.vector_store import VectorStore


class _FixedEmbeddingManager:
    def embed_text(self, text: str) -> list[float]:
        return [float(len(text) or 1), 1.0, 0.5]


class RetrievalFeedbackTests(unittest.TestCase):
    def test_feedback_is_append_only_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            persist_dir = str(Path(temp_dir) / "chroma")
            store = VectorStore(
                persist_dir=persist_dir,
                embedding_manager=_FixedEmbeddingManager(),
            )
            chunk_id = store.add_chunk("The support window is 9 to 5.")
            first = store.record_retrieval_feedback(
                chunk_id,
                "relevant",
                "Answered the scheduling question.",
            )
            second = store.record_retrieval_feedback(chunk_id, "superseded")

            self.assertNotEqual(first["feedback_id"], second["feedback_id"])
            self.assertFalse(store.get_chunk(chunk_id).metadata["deprecated"])

            reopened = VectorStore(
                persist_dir=persist_dir,
                embedding_manager=_FixedEmbeddingManager(),
            )
            self.assertEqual(reopened.get_retrieval_feedback(first["feedback_id"]), first)
            self.assertEqual(reopened.get_retrieval_feedback(second["feedback_id"]), second)

    def test_tool_validates_values_and_reports_no_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VectorStore(
                persist_dir=str(Path(temp_dir) / "chroma"),
                embedding_manager=_FixedEmbeddingManager(),
            )
            chunk_id = store.add_chunk("A search result.")
            with patch("src.mcp_server_v1._ensure_ready", return_value=(store, None, None)):
                result = record_retrieval_feedback(chunk_id, "irrelevant", "Wrong topic")
                invalid = record_retrieval_feedback(chunk_id, "maybe")

            self.assertTrue(result["recorded"])
            self.assertFalse(result["ranking_changed"])
            self.assertFalse(result["chunk_updated"])
            self.assertFalse(invalid["recorded"])
            self.assertIn("relevant, irrelevant, superseded", invalid["error"])
            self.assertEqual(store.feedback_collection.count(), 1)

            with self.assertRaisesRegex(ValueError, "existing memory chunk"):
                store.record_retrieval_feedback("missing", "relevant")
            with self.assertRaisesRegex(ValueError, "2000 characters"):
                store.record_retrieval_feedback(chunk_id, "relevant", "x" * 2001)


if __name__ == "__main__":
    unittest.main()
